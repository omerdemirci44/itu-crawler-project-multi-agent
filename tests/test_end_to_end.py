"""Deterministic end-to-end integration test for the crawler/search MVP."""

from __future__ import annotations

from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest

from app.crawler import crawl_job
from app import index_store
from app.search import search_query
from app.status import format_status_text, get_job_status


class _FixtureSiteHandler(BaseHTTPRequestHandler):
    """Serve a tiny deterministic website for the integration test."""

    routes: dict[str, tuple[str, bytes]] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Serve the configured route or return 404."""

        content_type, body = self.routes.get(
            self.path,
            ("text/plain", b"not found"),
        )
        status = 200 if self.path in self.routes else 404

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        """Silence request logs so test output stays deterministic."""


class EndToEndCrawlerTest(unittest.TestCase):
    """Exercise the real storage, crawler, search, and status flow together."""

    def setUp(self) -> None:
        """Create a temporary database and start the local fixture site."""

        super().setUp()
        repo_root = Path(__file__).resolve().parents[1]
        data_dir = repo_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # Keep the temporary database file under the workspace so the test
        # works in restricted environments that do not allow writes to the
        # system temp directory.
        db_file_descriptor, db_file_path = tempfile.mkstemp(
            dir=str(data_dir),
            prefix="test_end_to_end_",
            suffix=".db",
        )
        os.close(db_file_descriptor)
        self.db_path = Path(db_file_path)
        self.addCleanup(self._remove_database_files)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureSiteHandler)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            name="fixture-http-site",
            daemon=True,
        )
        self.server_thread.start()
        self.addCleanup(self._stop_server)

        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def _stop_server(self) -> None:
        """Shut down the fixture HTTP server cleanly."""

        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)

    def _remove_database_files(self) -> None:
        """Remove the temporary SQLite database and its WAL sidecar files."""

        for suffix in ("", "-shm", "-wal"):
            file_path = Path(f"{self.db_path}{suffix}")
            if file_path.exists():
                file_path.unlink()

    def _site_url(self, path: str) -> str:
        """Build one absolute URL for the local fixture site."""

        return f"{self.base_url}{path}"

    def test_crawl_search_and_status_end_to_end(self) -> None:
        """Crawl a tiny local site and verify practical MVP behavior."""

        _FixtureSiteHandler.routes = {
            "/index.html": (
                "text/html",
                b"""
                <html>
                  <head><title>Origin Hub</title></head>
                  <body>
                    <p>Origin page for the deterministic crawler test.</p>
                    <a href="/alpha.html">Alpha</a>
                    <a href="/alpha.html#fragment">Alpha Duplicate</a>
                    <a href="/beta.html">Beta</a>
                    <a href="/notes.txt">Notes</a>
                  </body>
                </html>
                """,
            ),
            "/alpha.html": (
                "text/html",
                b"""
                <html>
                  <head><title>Alpha Page</title></head>
                  <body>
                    <p>Alpha content is indexed.</p>
                    <a href="/gamma.html">Gamma</a>
                  </body>
                </html>
                """,
            ),
            "/beta.html": (
                "text/html",
                b"""
                <html>
                  <head><title>Beta Page</title></head>
                  <body>
                    <p>Beta also links to the shared child.</p>
                    <a href="/gamma.html">Gamma Again</a>
                  </body>
                </html>
                """,
            ),
            "/gamma.html": (
                "text/html",
                b"""
                <html>
                  <head><title>Gamma Page</title></head>
                  <body>
                    <p>LayeredSignal only appears on this page.</p>
                    <a href="/index.html">Origin Loop</a>
                  </body>
                </html>
                """,
            ),
            "/notes.txt": (
                "text/plain",
                b"skiptoken should never be indexed because this is plain text",
            ),
        }

        origin_url = self._site_url("/index.html")
        job_id = index_store.create_crawl_job(
            db_path=self.db_path,
            origin_url=origin_url,
            max_depth=2,
        )

        counts = crawl_job(str(self.db_path), job_id)

        self.assertEqual(counts["total_pages"], 5)
        self.assertEqual(counts["indexed_pages"], 4)
        self.assertEqual(counts["skipped_pages"], 1)
        self.assertEqual(counts["failed_pages"], 0)

        with closing(index_store.get_connection(self.db_path)) as connection:
            page_rows = connection.execute(
                """
                SELECT canonical_url, depth, state
                FROM page
                WHERE job_id = ?
                ORDER BY canonical_url ASC
                """,
                (job_id,),
            ).fetchall()

            page_content_rows = connection.execute(
                """
                SELECT p.canonical_url
                FROM page_content AS pc
                INNER JOIN page AS p
                    ON p.id = pc.page_id
                WHERE p.job_id = ?
                ORDER BY p.canonical_url ASC
                """,
                (job_id,),
            ).fetchall()

        pages_by_url = {
            str(row["canonical_url"]): {
                "depth": int(row["depth"]),
                "state": str(row["state"]),
            }
            for row in page_rows
        }
        indexed_urls = [str(row["canonical_url"]) for row in page_content_rows]

        origin_page_url = self._site_url("/index.html")
        alpha_url = self._site_url("/alpha.html")
        beta_url = self._site_url("/beta.html")
        gamma_url = self._site_url("/gamma.html")
        notes_url = self._site_url("/notes.txt")

        self.assertIn(origin_page_url, pages_by_url)
        self.assertIn(alpha_url, pages_by_url)
        self.assertIn(beta_url, pages_by_url)
        self.assertIn(gamma_url, pages_by_url)
        self.assertIn(notes_url, pages_by_url)

        self.assertEqual(pages_by_url[origin_page_url]["depth"], 0)
        self.assertEqual(pages_by_url[alpha_url]["depth"], 1)
        self.assertEqual(pages_by_url[beta_url]["depth"], 1)
        self.assertEqual(pages_by_url[gamma_url]["depth"], 2)
        self.assertEqual(pages_by_url[notes_url]["depth"], 1)

        # Duplicate links should still produce exactly one stored page row.
        self.assertEqual(
            sum(1 for row in page_rows if str(row["canonical_url"]) == alpha_url),
            1,
        )
        self.assertEqual(
            sum(1 for row in page_rows if str(row["canonical_url"]) == gamma_url),
            1,
        )

        self.assertEqual(pages_by_url[notes_url]["state"], "skipped")
        self.assertNotIn(notes_url, indexed_urls)

        search_results = search_query(self.db_path, "LayeredSignal", limit=10)
        self.assertEqual(search_results, [(gamma_url, origin_url, 2)])

        skipped_search_results = search_query(self.db_path, "skiptoken", limit=10)
        self.assertEqual(skipped_search_results, [])

        status_data = get_job_status(self.db_path, job_id)
        status_text = format_status_text(status_data)

        self.assertIn(f"Crawl Job {job_id}", status_text)
        self.assertIn("Status: completed_with_errors", status_text)
        self.assertIn(f"Origin URL: {origin_url}", status_text)
        self.assertIn("Indexed: 4", status_text)
        self.assertIn("Skipped: 1", status_text)


if __name__ == "__main__":
    unittest.main()
