"""SQLite storage helpers for the crawler/search MVP.

This module owns the local database schema and a small set of helper
functions that other modules can call. It intentionally does not implement
crawler coordination, parsing, or search behavior.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


JOB_STATUSES = (
    "pending",
    "running",
    "completed",
    "completed_with_errors",
    "failed",
    "stopped",
)

PAGE_STATES = (
    "queued",
    "leased",
    "fetched",
    "indexed",
    "failed",
    "skipped",
)


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS crawl_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_url TEXT NOT NULL,
    max_depth INTEGER NOT NULL CHECK (max_depth >= 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN {JOB_STATUSES}),
    current_depth INTEGER NOT NULL DEFAULT 0 CHECK (current_depth >= 0),
    worker_count INTEGER NOT NULL CHECK (worker_count > 0),
    queue_capacity INTEGER NOT NULL CHECK (queue_capacity > 0),
    request_timeout_sec INTEGER NOT NULL CHECK (request_timeout_sec > 0),
    max_page_bytes INTEGER NOT NULL CHECK (max_page_bytes > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS page (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    canonical_url TEXT NOT NULL,
    origin_url TEXT NOT NULL,
    depth INTEGER NOT NULL CHECK (depth >= 0),
    parent_url TEXT,
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN {PAGE_STATES}),
    http_status INTEGER,
    content_type TEXT,
    final_url TEXT,
    title TEXT,
    error_reason TEXT,
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fetched_at TEXT,
    indexed_at TEXT,
    FOREIGN KEY (job_id) REFERENCES crawl_job(id) ON DELETE CASCADE,
    UNIQUE (job_id, canonical_url)
);

CREATE TABLE IF NOT EXISTS page_content (
    page_id INTEGER PRIMARY KEY,
    title TEXT,
    body_text TEXT NOT NULL DEFAULT '',
    stored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES page(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS crawl_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    page_id INTEGER,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES crawl_job(id) ON DELETE CASCADE,
    FOREIGN KEY (page_id) REFERENCES page(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_page_job_state
    ON page (job_id, state);

CREATE INDEX IF NOT EXISTS idx_page_job_depth_state
    ON page (job_id, depth, state);

CREATE INDEX IF NOT EXISTS idx_crawl_event_job_created
    ON crawl_event (job_id, created_at);
"""


def _ensure_parent_directory(db_path: str | Path) -> None:
    """Create the parent directory for a filesystem-backed SQLite database."""

    if str(db_path) == ":memory:":
        return

    Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _validate_non_empty_text(value: str, field_name: str) -> None:
    """Raise a ValueError when a required text field is blank."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_non_negative_int(value: int, field_name: str) -> None:
    """Raise a ValueError when an integer field is negative."""

    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")


def _validate_positive_int(value: int, field_name: str) -> None:
    """Raise a ValueError when an integer field is zero or negative."""

    if value <= 0:
        raise ValueError(f"{field_name} must be > 0")


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Return a configured SQLite connection for the crawler database.

    The connection enables foreign keys, configures a busy timeout, and
    requests WAL mode so readers can continue working while writes are active.
    Callers are responsible for closing the returned connection.
    """

    _ensure_parent_directory(db_path)

    connection = sqlite3.connect(str(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row

    # Apply connection-level settings on every open.
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.execute("PRAGMA busy_timeout = 5000;")
    connection.execute("PRAGMA journal_mode = WAL;")
    connection.execute("PRAGMA synchronous = NORMAL;")

    return connection


def initialize_schema(db_path: str | Path) -> None:
    """Create the MVP schema if it does not already exist."""

    with closing(get_connection(db_path)) as connection:
        with connection:
            connection.executescript(SCHEMA_SQL)


def create_crawl_job(
    db_path: str | Path,
    origin_url: str,
    max_depth: int,
    worker_count: int = 8,
    queue_capacity: int = 16,
    request_timeout_sec: int = 5,
    max_page_bytes: int = 1048576,
) -> int:
    """Create and return a new crawl job row.

    The job starts in the `running` state because this helper is intended to be
    called when the operator starts a crawl from the CLI.
    """

    _validate_non_empty_text(origin_url, "origin_url")
    _validate_non_negative_int(max_depth, "max_depth")
    _validate_positive_int(worker_count, "worker_count")
    _validate_positive_int(queue_capacity, "queue_capacity")
    _validate_positive_int(request_timeout_sec, "request_timeout_sec")
    _validate_positive_int(max_page_bytes, "max_page_bytes")

    initialize_schema(db_path)

    with closing(get_connection(db_path)) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO crawl_job (
                    origin_url,
                    max_depth,
                    status,
                    current_depth,
                    worker_count,
                    queue_capacity,
                    request_timeout_sec,
                    max_page_bytes,
                    started_at
                )
                VALUES (?, ?, 'running', 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    origin_url,
                    max_depth,
                    worker_count,
                    queue_capacity,
                    request_timeout_sec,
                    max_page_bytes,
                ),
            )
            return int(cursor.lastrowid)


def insert_origin_page(db_path: str | Path, job_id: int, origin_url: str) -> int:
    """Insert the origin page for a crawl job at depth 0.

    Returns the page id for the stored row. If the row already exists, the
    existing page id is returned.
    """

    return insert_discovered_page(
        db_path=db_path,
        job_id=job_id,
        canonical_url=origin_url,
        origin_url=origin_url,
        depth=0,
        parent_url=None,
    )


def insert_discovered_page(
    db_path: str | Path,
    job_id: int,
    canonical_url: str,
    origin_url: str,
    depth: int,
    parent_url: str | None = None,
) -> int:
    """Insert a discovered page if it is new for the job.

    Duplicate inserts are safe because the schema enforces
    `UNIQUE(job_id, canonical_url)`. When a duplicate is found, the existing
    row is kept and its id is returned. If the new discovery has a smaller
    depth than the stored row, the stored depth is updated to the smaller one.
    """

    _validate_positive_int(job_id, "job_id")
    _validate_non_empty_text(canonical_url, "canonical_url")
    _validate_non_empty_text(origin_url, "origin_url")
    _validate_non_negative_int(depth, "depth")

    initialize_schema(db_path)

    with closing(get_connection(db_path)) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO page (
                    job_id,
                    canonical_url,
                    origin_url,
                    depth,
                    parent_url,
                    state
                )
                VALUES (?, ?, ?, ?, ?, 'queued')
                """,
                (job_id, canonical_url, origin_url, depth, parent_url),
            )

            if cursor.rowcount == 1:
                return int(cursor.lastrowid)

            # A duplicate insert is expected during crawl fan-out. Re-read the
            # existing row so the caller still gets a stable page id.
            row = connection.execute(
                """
                SELECT id, depth, parent_url
                FROM page
                WHERE job_id = ? AND canonical_url = ?
                """,
                (job_id, canonical_url),
            ).fetchone()

            if row is None:
                raise sqlite3.IntegrityError(
                    "page insert was ignored but the existing row could not be read"
                )

            page_id = int(row["id"])
            existing_depth = int(row["depth"])
            existing_parent_url = row["parent_url"]

            # Keep the shallowest known depth for the job. This matches the docs
            # and keeps the storage layer safe even if callers discover duplicates.
            if depth < existing_depth:
                connection.execute(
                    """
                    UPDATE page
                    SET depth = ?, parent_url = COALESCE(?, parent_url)
                    WHERE id = ?
                    """,
                    (depth, parent_url, page_id),
                )
            elif existing_parent_url is None and parent_url is not None:
                connection.execute(
                    "UPDATE page SET parent_url = ? WHERE id = ?",
                    (parent_url, page_id),
                )

            return page_id


def update_page_state(
    db_path: str | Path,
    page_id: int,
    state: str,
    http_status: int | None = None,
    error_reason: str | None = None,
    final_url: str | None = None,
    content_type: str | None = None,
    title: str | None = None,
) -> None:
    """Update a page's lifecycle state and optional fetch metadata.

    Optional fields are only written when provided so callers do not
    accidentally erase previously stored metadata.
    """

    _validate_positive_int(page_id, "page_id")
    if state not in PAGE_STATES:
        raise ValueError(f"state must be one of {PAGE_STATES}")

    initialize_schema(db_path)

    assignments = ["state = ?"]
    parameters: list[Any] = [state]

    if http_status is not None:
        assignments.append("http_status = ?")
        parameters.append(http_status)
    if error_reason is not None:
        assignments.append("error_reason = ?")
        parameters.append(error_reason)
    if final_url is not None:
        assignments.append("final_url = ?")
        parameters.append(final_url)
    if content_type is not None:
        assignments.append("content_type = ?")
        parameters.append(content_type)
    if title is not None:
        assignments.append("title = ?")
        parameters.append(title)

    # `fetched_at` is populated the first time a fetch-related terminal state
    # is written. `indexed_at` is populated when the page is marked indexed.
    assignments.append(
        """
        fetched_at = CASE
            WHEN ? IN ('fetched', 'indexed', 'failed', 'skipped')
            THEN COALESCE(fetched_at, CURRENT_TIMESTAMP)
            ELSE fetched_at
        END
        """.strip()
    )
    parameters.append(state)

    assignments.append(
        """
        indexed_at = CASE
            WHEN ? = 'indexed' THEN CURRENT_TIMESTAMP
            ELSE indexed_at
        END
        """.strip()
    )
    parameters.append(state)

    parameters.append(page_id)

    with closing(get_connection(db_path)) as connection:
        with connection:
            cursor = connection.execute(
                f"UPDATE page SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                raise ValueError(f"page_id {page_id} does not exist")


def store_page_content(
    db_path: str | Path,
    page_id: int,
    title: str | None,
    body_text: str,
) -> None:
    """Insert or replace extracted page text for a page id.

    Content storage is separate from page state changes so the crawler can
    decide when a page should be considered fully indexed.
    """

    _validate_positive_int(page_id, "page_id")
    if body_text is None:
        raise ValueError("body_text must not be None")

    initialize_schema(db_path)

    with closing(get_connection(db_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO page_content (page_id, title, body_text, stored_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(page_id) DO UPDATE SET
                    title = excluded.title,
                    body_text = excluded.body_text,
                    stored_at = CURRENT_TIMESTAMP
                """,
                (page_id, title, body_text),
            )

            # Keep the page title in sync with stored content when a title is known.
            if title is not None:
                cursor = connection.execute(
                    "UPDATE page SET title = ? WHERE id = ?",
                    (title, page_id),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"page_id {page_id} does not exist")


def get_status_counts(db_path: str | Path, job_id: int) -> dict[str, Any]:
    """Return job metadata and page state counts for a crawl job."""

    _validate_positive_int(job_id, "job_id")
    initialize_schema(db_path)

    with closing(get_connection(db_path)) as connection:
        row = connection.execute(
            """
            SELECT
                cj.id AS job_id,
                cj.origin_url,
                cj.max_depth,
                cj.status,
                cj.current_depth,
                cj.created_at,
                cj.started_at,
                cj.finished_at,
                COALESCE(COUNT(p.id), 0) AS total_pages,
                COALESCE(SUM(CASE WHEN p.state = 'queued' THEN 1 ELSE 0 END), 0) AS queued_pages,
                COALESCE(SUM(CASE WHEN p.state = 'leased' THEN 1 ELSE 0 END), 0) AS leased_pages,
                COALESCE(SUM(CASE WHEN p.state = 'fetched' THEN 1 ELSE 0 END), 0) AS fetched_pages,
                COALESCE(SUM(CASE WHEN p.state = 'indexed' THEN 1 ELSE 0 END), 0) AS indexed_pages,
                COALESCE(SUM(CASE WHEN p.state = 'failed' THEN 1 ELSE 0 END), 0) AS failed_pages,
                COALESCE(SUM(CASE WHEN p.state = 'skipped' THEN 1 ELSE 0 END), 0) AS skipped_pages
            FROM crawl_job AS cj
            LEFT JOIN page AS p
                ON p.job_id = cj.id
            WHERE cj.id = ?
            GROUP BY
                cj.id,
                cj.origin_url,
                cj.max_depth,
                cj.status,
                cj.current_depth,
                cj.created_at,
                cj.started_at,
                cj.finished_at
            """,
            (job_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"job_id {job_id} does not exist")

    result = dict(row)
    result["pages_crawled"] = (
        result["fetched_pages"]
        + result["indexed_pages"]
        + result["failed_pages"]
        + result["skipped_pages"]
    )
    result["queue_depth"] = result["queued_pages"]
    result["is_active"] = result["status"] == "running"
    return result


__all__ = [
    "create_crawl_job",
    "get_connection",
    "get_status_counts",
    "initialize_schema",
    "insert_discovered_page",
    "insert_origin_page",
    "store_page_content",
    "update_page_state",
]
