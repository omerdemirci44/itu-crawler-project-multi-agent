"""Lightweight localhost web UI for the crawler/search MVP.

The server intentionally stays small and standard-library only. It exposes a
minimal HTML interface on top of the existing storage, crawler, search, and
status modules without changing their behavior.
"""

from __future__ import annotations

import html
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from . import index_store
from .crawler import crawl_job
from .parser import normalize_url
from .search import search_query
from .status import format_status_text, get_job_status


_DEFAULT_DB_PATH = "data/crawler.db"
_DEFAULT_SEARCH_LIMIT = 10
_MAX_FORM_BYTES = 16 * 1024

_BACKGROUND_CRAWLS: dict[tuple[str, int], threading.Thread] = {}
_BACKGROUND_CRAWLS_LOCK = threading.Lock()

_PAGE_STYLE = """
body {
    margin: 0;
    background: #f5efe6;
    color: #1f2933;
    font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
}
main {
    max-width: 960px;
    margin: 0 auto;
    padding: 32px 20px 48px;
}
h1, h2 {
    margin: 0 0 14px;
    color: #112233;
}
p {
    line-height: 1.55;
}
a {
    color: #0f5f8c;
}
.hero {
    background: linear-gradient(135deg, #fffaf2, #f1e3d3);
    border: 1px solid #dcc5aa;
    border-radius: 18px;
    padding: 24px;
    box-shadow: 0 10px 30px rgba(58, 45, 32, 0.08);
}
.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 18px;
    margin-top: 22px;
}
.card {
    background: #fffdf9;
    border: 1px solid #dfd3c2;
    border-radius: 16px;
    padding: 18px;
    box-shadow: 0 8px 24px rgba(39, 32, 24, 0.06);
}
label {
    display: block;
    font-size: 0.95rem;
    font-weight: 600;
    margin-top: 12px;
    margin-bottom: 6px;
}
input {
    width: 100%;
    box-sizing: border-box;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid #c9baa7;
    background: #ffffff;
    color: #18222c;
}
button {
    margin-top: 14px;
    border: 0;
    border-radius: 999px;
    background: #1d6f5f;
    color: #ffffff;
    padding: 11px 18px;
    font-weight: 700;
    cursor: pointer;
}
button:hover {
    background: #185b4f;
}
.notice {
    background: #fff7e7;
    border: 1px solid #e4c88b;
    border-radius: 14px;
    padding: 16px;
    margin-bottom: 20px;
}
.meta {
    color: #556270;
    font-size: 0.95rem;
}
.result-list {
    list-style: none;
    padding: 0;
    margin: 18px 0 0;
}
.result-item {
    background: #fffdf9;
    border: 1px solid #dfd3c2;
    border-radius: 14px;
    padding: 14px 16px;
    margin-bottom: 10px;
}
pre {
    background: #17202a;
    color: #f8f3ea;
    padding: 18px;
    border-radius: 16px;
    overflow-x: auto;
    line-height: 1.45;
}
.footer-links {
    margin-top: 22px;
}
"""


def _escape(value: Any) -> str:
    """Return an HTML-escaped string for templates."""

    return html.escape(str(value), quote=True)


def _canonical_db_path(db_path: str | Path) -> str:
    """Return a stable string key for in-memory background thread tracking."""

    return str(Path(db_path).expanduser())


def _thread_key(db_path: str | Path, job_id: int) -> tuple[str, int]:
    """Build the background-thread registry key for one job."""

    return (_canonical_db_path(db_path), int(job_id))


def _register_background_crawl(
    db_path: str | Path,
    job_id: int,
    thread: threading.Thread,
) -> None:
    """Store a started crawl thread in the in-memory registry."""

    with _BACKGROUND_CRAWLS_LOCK:
        _BACKGROUND_CRAWLS[_thread_key(db_path, job_id)] = thread


def _unregister_background_crawl(db_path: str | Path, job_id: int) -> None:
    """Remove a crawl thread from the in-memory registry."""

    with _BACKGROUND_CRAWLS_LOCK:
        _BACKGROUND_CRAWLS.pop(_thread_key(db_path, job_id), None)


def _is_background_crawl_running(db_path: str | Path, job_id: int) -> bool:
    """Return ``True`` when this server still tracks a live crawl thread."""

    with _BACKGROUND_CRAWLS_LOCK:
        thread = _BACKGROUND_CRAWLS.get(_thread_key(db_path, job_id))
    return bool(thread and thread.is_alive())


def _parse_non_empty_text(value: str, field_name: str) -> str:
    """Validate a required text input and return the stripped value."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _parse_positive_int(value: str, field_name: str) -> int:
    """Parse a positive integer from user input."""

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer") from exc

    if parsed <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return parsed


def _parse_non_negative_int(value: str, field_name: str) -> int:
    """Parse a non-negative integer from user input."""

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer") from exc

    if parsed < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return parsed


def _render_document(title: str, body_html: str) -> str:
    """Wrap page content in a simple reusable HTML shell."""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
  <style>{_PAGE_STYLE}</style>
</head>
<body>
  <main>
    {body_html}
  </main>
</body>
</html>
"""


def _render_home_page() -> str:
    """Return the minimal demo-friendly landing page."""

    body_html = f"""
<section class="hero">
  <h1>Localhost Web Crawler MVP</h1>
  <p>
    This demo UI starts crawl jobs in the background, searches committed pages,
    and renders crawl status directly from the SQLite database.
  </p>
  <p class="meta">
    Standard-library only: <code>http.server</code>, <code>threading</code>,
    <code>sqlite3</code>, and the existing application modules.
  </p>
</section>

<section class="grid">
  <section class="card">
    <h2>Start Crawl</h2>
    <form method="post" action="/crawl">
      <label for="crawl-db">Database path</label>
      <input id="crawl-db" name="db" value="{_escape(_DEFAULT_DB_PATH)}">

      <label for="crawl-origin">Origin URL</label>
      <input id="crawl-origin" name="origin" placeholder="http://localhost:9000/index.html">

      <label for="crawl-depth">Max depth</label>
      <input id="crawl-depth" name="depth" value="1">

      <button type="submit">Start Background Crawl</button>
    </form>
  </section>

  <section class="card">
    <h2>Search</h2>
    <form method="get" action="/search">
      <label for="search-db">Database path</label>
      <input id="search-db" name="db" value="{_escape(_DEFAULT_DB_PATH)}">

      <label for="search-query">Query</label>
      <input id="search-query" name="query" placeholder="crawler">

      <label for="search-limit">Result limit</label>
      <input id="search-limit" name="limit" value="{_escape(_DEFAULT_SEARCH_LIMIT)}">

      <button type="submit">Run Search</button>
    </form>
  </section>

  <section class="card">
    <h2>View Status</h2>
    <form method="get" action="/status">
      <label for="status-db">Database path</label>
      <input id="status-db" name="db" value="{_escape(_DEFAULT_DB_PATH)}">

      <label for="status-job">Job ID</label>
      <input id="status-job" name="job" placeholder="1">

      <button type="submit">Show Status</button>
    </form>
  </section>
</section>
"""
    return _render_document("Crawler MVP", body_html)


def _render_error_page(title: str, message: str) -> str:
    """Return a readable HTML error page."""

    body_html = f"""
<section class="notice">
  <h1>{_escape(title)}</h1>
  <p>{_escape(message)}</p>
</section>
<p class="footer-links"><a href="/">Back to home</a></p>
"""
    return _render_document(title, body_html)


def _render_crawl_started_page(db_path: str, job_id: int) -> str:
    """Return the confirmation page for a newly started crawl job."""

    status_url = "/status?" + urlencode({"db": db_path, "job": job_id})
    body_html = f"""
<section class="notice">
  <h1>Crawl started</h1>
  <p>Created crawl job <strong>{job_id}</strong> and launched it in a background thread.</p>
  <p class="meta">Database: <code>{_escape(db_path)}</code></p>
</section>
<p><a href="{_escape(status_url)}">Open status for job {job_id}</a></p>
<p class="footer-links"><a href="/">Back to home</a></p>
"""
    return _render_document(f"Crawl Job {job_id}", body_html)


def _render_search_page(
    db_path: str,
    query: str,
    limit: int,
    results: list[tuple[str, str, int]],
) -> str:
    """Return HTML for search results."""

    if results:
        result_items = "\n".join(
            (
                '<li class="result-item"><code>('
                f"{_escape(relevant_url)}, {_escape(origin_url)}, {depth}"
                ")</code></li>"
            )
            for relevant_url, origin_url, depth in results
        )
    else:
        result_items = '<li class="result-item">No results</li>'

    body_html = f"""
<section class="hero">
  <h1>Search Results</h1>
  <p><strong>Query:</strong> <code>{_escape(query)}</code></p>
  <p class="meta">
    Database: <code>{_escape(db_path)}</code> |
    Limit: <code>{limit}</code> |
    Returned: <code>{len(results)}</code>
  </p>
</section>
<ul class="result-list">
  {result_items}
</ul>
<p class="footer-links"><a href="/">Back to home</a></p>
"""
    return _render_document("Search Results", body_html)


def _render_status_page(
    db_path: str,
    job_id: int,
    status_text: str,
    thread_running: bool,
) -> str:
    """Return HTML for one crawl-job status view."""

    body_html = f"""
<section class="hero">
  <h1>Job Status</h1>
  <p class="meta">
    Database: <code>{_escape(db_path)}</code> |
    Job: <code>{job_id}</code> |
    Background thread on this server: <code>{"running" if thread_running else "not tracked"}</code>
  </p>
</section>
<pre>{_escape(status_text)}</pre>
<p class="footer-links"><a href="/">Back to home</a></p>
"""
    return _render_document(f"Status {job_id}", body_html)


def _start_background_crawl(db_path: str, job_id: int) -> None:
    """Launch the crawler in a daemon thread and register it in memory."""

    def runner() -> None:
        try:
            crawl_job(db_path, job_id)
        except Exception as exc:
            # Keep failures visible in the database so the status UI remains
            # useful even if the background thread crashes unexpectedly.
            try:
                index_store.record_crawl_event(
                    db_path=db_path,
                    job_id=job_id,
                    event_type="job_failed",
                    message=(
                        "background crawl crashed: "
                        f"{exc.__class__.__name__}: {exc}"
                    ),
                )
                index_store.complete_crawl_job(db_path, job_id, status="failed")
            except Exception:
                pass
        finally:
            _unregister_background_crawl(db_path, job_id)

    thread = threading.Thread(
        target=runner,
        name=f"crawl-job-{job_id}",
        daemon=True,
    )
    _register_background_crawl(db_path, job_id, thread)
    thread.start()


class CrawlerHTTPRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for the minimal crawler demo UI."""

    server_version = "ITUCrawlerMVP/0.1"

    def do_GET(self) -> None:
        """Dispatch supported read routes."""

        parsed = urlparse(self.path)

        try:
            if parsed.path == "/":
                self._send_html(HTTPStatus.OK, _render_home_page())
                return
            if parsed.path == "/search":
                self._handle_search(parsed)
                return
            if parsed.path == "/status":
                self._handle_status(parsed)
                return
            if parsed.path == "/health":
                self._send_text(HTTPStatus.OK, "ok")
                return

            self._send_error_page(
                HTTPStatus.NOT_FOUND,
                "Page not found",
                f"Unsupported route: {parsed.path}",
            )
        except ValueError as exc:
            self._send_error_page(HTTPStatus.BAD_REQUEST, "Invalid request", str(exc))
        except Exception as exc:
            self._send_error_page(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Server error",
                f"{exc.__class__.__name__}: {exc}",
            )

    def do_POST(self) -> None:
        """Dispatch supported write routes."""

        parsed = urlparse(self.path)

        try:
            if parsed.path == "/crawl":
                self._handle_crawl()
                return

            self._send_error_page(
                HTTPStatus.NOT_FOUND,
                "Page not found",
                f"Unsupported route: {parsed.path}",
            )
        except ValueError as exc:
            self._send_error_page(HTTPStatus.BAD_REQUEST, "Invalid request", str(exc))
        except Exception as exc:
            self._send_error_page(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Server error",
                f"{exc.__class__.__name__}: {exc}",
            )

    def _handle_crawl(self) -> None:
        """Validate form input, create a job, and start background crawling."""

        form = self._read_form_body()
        db_path = _parse_non_empty_text(self._require_param(form, "db"), "db")
        origin = _parse_non_empty_text(self._require_param(form, "origin"), "origin")
        depth = _parse_non_negative_int(self._require_param(form, "depth"), "depth")

        normalized_origin = normalize_url(origin)
        if normalized_origin is None:
            raise ValueError("origin must be a valid http or https URL")

        index_store.initialize_schema(db_path)
        job_id = index_store.create_crawl_job(
            db_path=db_path,
            origin_url=origin,
            max_depth=depth,
        )
        index_store.insert_discovered_page(
            db_path=db_path,
            job_id=job_id,
            canonical_url=normalized_origin,
            origin_url=origin,
            depth=0,
            parent_url=None,
        )

        _start_background_crawl(db_path, job_id)
        self._send_html(HTTPStatus.OK, _render_crawl_started_page(db_path, job_id))

    def _handle_search(self, parsed) -> None:
        """Run a search query and render result triples."""

        params = parse_qs(parsed.query, keep_blank_values=True)
        db_path = _parse_non_empty_text(self._require_param(params, "db"), "db")
        query = _parse_non_empty_text(self._require_param(params, "query"), "query")
        limit_text = params.get("limit", [str(_DEFAULT_SEARCH_LIMIT)])[0]
        limit = _parse_positive_int(limit_text, "limit")

        results = search_query(db_path=db_path, query=query, limit=limit)
        self._send_html(HTTPStatus.OK, _render_search_page(db_path, query, limit, results))

    def _handle_status(self, parsed) -> None:
        """Render formatted crawl status for one job."""

        params = parse_qs(parsed.query, keep_blank_values=True)
        db_path = _parse_non_empty_text(self._require_param(params, "db"), "db")
        job_id = _parse_positive_int(self._require_param(params, "job"), "job")

        status_data = get_job_status(db_path=db_path, job_id=job_id)
        status_text = format_status_text(status_data)
        self._send_html(
            HTTPStatus.OK,
            _render_status_page(
                db_path=db_path,
                job_id=job_id,
                status_text=status_text,
                thread_running=_is_background_crawl_running(db_path, job_id),
            ),
        )

    def _read_form_body(self) -> dict[str, list[str]]:
        """Parse a URL-encoded request body into a query-style mapping."""

        content_length_text = self.headers.get("Content-Length", "0").strip() or "0"

        try:
            content_length = int(content_length_text)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc

        if content_length < 0:
            raise ValueError("Content-Length must be >= 0")
        if content_length > _MAX_FORM_BYTES:
            raise ValueError("request body is too large for this demo server")

        body = self.rfile.read(content_length)
        return parse_qs(body.decode("utf-8"), keep_blank_values=True)

    def _require_param(self, params: dict[str, list[str]], name: str) -> str:
        """Return one request parameter value or raise a readable error."""

        values = params.get(name)
        if not values:
            raise ValueError(f"{name} is required")
        return values[0]

    def _send_html(self, status: HTTPStatus, html_text: str) -> None:
        """Send an HTML response with UTF-8 encoding."""

        payload = html_text.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        """Send a plain-text response with UTF-8 encoding."""

        payload = text.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_error_page(
        self,
        status: HTTPStatus,
        title: str,
        message: str,
    ) -> None:
        """Send a styled HTML error response."""

        self._send_html(status, _render_error_page(title, message))


class _CrawlerHTTPServer(ThreadingHTTPServer):
    """Small ThreadingHTTPServer subclass with practical defaults."""

    daemon_threads = True
    allow_reuse_address = True


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the demo web server until interrupted."""

    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")
    if not isinstance(port, int) or isinstance(port, bool) or port <= 0 or port > 65535:
        raise ValueError("port must be an integer between 1 and 65535")

    server = _CrawlerHTTPServer((host, port), CrawlerHTTPRequestHandler)
    print(f"Serving crawler UI at http://{host}:{port}/")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.server_close()


__all__ = ["run_server"]
