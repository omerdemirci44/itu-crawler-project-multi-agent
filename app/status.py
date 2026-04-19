"""Read-only crawl status helpers for the MVP.

This module intentionally stays on the read path only. It builds practical
status snapshots from the SQLite database and formats them for a future CLI
without introducing any command-line or HTTP concerns here.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from . import index_store


def _validate_recent_event_limit(recent_event_limit: int) -> None:
    """Raise ``ValueError`` when the requested event limit is negative."""

    if recent_event_limit < 0:
        raise ValueError("recent_event_limit must be >= 0")


def _build_queue_snapshot(
    db_path: str | Path,
    job_id: int,
    current_depth: int,
    total_queued: int,
) -> dict[str, Any]:
    """Return deterministic queue details grouped by BFS depth."""

    with closing(index_store.get_connection(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT depth, COUNT(*) AS queued_pages
            FROM page
            WHERE job_id = ? AND state = 'queued'
            GROUP BY depth
            ORDER BY depth ASC
            """,
            (job_id,),
        ).fetchall()

    by_depth = [
        {
            "depth": int(row["depth"]),
            "queued_pages": int(row["queued_pages"]),
        }
        for row in rows
    ]

    queued_at_current_depth = 0
    next_depth_with_queued_pages = None
    queued_at_next_depth = 0

    for entry in by_depth:
        depth = int(entry["depth"])
        queued_pages = int(entry["queued_pages"])

        if depth == current_depth:
            queued_at_current_depth = queued_pages
        elif depth > current_depth and next_depth_with_queued_pages is None:
            next_depth_with_queued_pages = depth
            queued_at_next_depth = queued_pages

    return {
        "total_queued": total_queued,
        "current_depth": current_depth,
        "queued_at_current_depth": queued_at_current_depth,
        "next_depth_with_queued_pages": next_depth_with_queued_pages,
        "queued_at_next_depth": queued_at_next_depth,
        "queued_by_depth": by_depth,
    }


def _build_activity_snapshot(
    status: str,
    worker_count: int,
    queued_pages: int,
    leased_pages: int,
) -> dict[str, Any]:
    """Return a compact view of whether crawl work looks active or idle."""

    is_running = status == "running"
    active_workers = min(leased_pages, worker_count)
    idle_workers = max(worker_count - active_workers, 0)
    is_idle = is_running and active_workers == 0

    if not is_running:
        activity_label = "not_running"
    elif is_idle:
        activity_label = "idle"
    else:
        activity_label = "active"

    return {
        "label": activity_label,
        "is_running": is_running,
        "is_idle": is_idle,
        "has_queued_pages": queued_pages > 0,
        "has_inflight_pages": leased_pages > 0,
        "active_workers": active_workers,
        "idle_workers": idle_workers,
        "worker_count": worker_count,
    }


def _get_recent_events(
    db_path: str | Path,
    job_id: int,
    recent_event_limit: int,
) -> list[dict[str, Any]]:
    """Return recent crawl events ordered newest first."""

    if recent_event_limit == 0:
        return []

    with closing(index_store.get_connection(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                ce.id,
                ce.created_at,
                ce.event_type,
                ce.message,
                ce.page_id,
                p.canonical_url AS page_url,
                p.depth AS page_depth
            FROM crawl_event AS ce
            LEFT JOIN page AS p
                ON p.id = ce.page_id
            WHERE ce.job_id = ?
            ORDER BY ce.created_at DESC, ce.id DESC
            LIMIT ?
            """,
            (job_id, recent_event_limit),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "created_at": row["created_at"],
            "event_type": row["event_type"],
            "message": row["message"],
            "page_id": int(row["page_id"]) if row["page_id"] is not None else None,
            "page_url": row["page_url"],
            "page_depth": (
                int(row["page_depth"]) if row["page_depth"] is not None else None
            ),
        }
        for row in rows
    ]


def get_job_status(
    db_path: str | Path,
    job_id: int,
    recent_event_limit: int = 10,
) -> dict[str, Any]:
    """Return a read-only status snapshot for one crawl job.

    The returned dictionary is grouped into stable sections so a future CLI or
    UI can reuse the same structure:

    - ``job``: metadata and crawl configuration
    - ``counts``: page-state counters derived from ``get_status_counts(...)``
    - ``queue``: queue depth details, including queued pages by BFS depth
    - ``activity``: active/idle snapshot based on job status and leased pages
    - ``recent_events``: newest-first crawl events

    Raises:
        ValueError: when ``job_id`` does not exist or ``recent_event_limit`` is
            negative.
    """

    _validate_recent_event_limit(recent_event_limit)

    # Use the storage-layer aggregate helper for the core counters required by
    # the MVP. This also gives us a clear ``ValueError`` when the job is absent.
    counts_row = index_store.get_status_counts(db_path, job_id)
    job_row = index_store.get_job(db_path, job_id)

    current_depth = int(job_row["current_depth"])
    queued_pages = int(counts_row["queued_pages"])
    leased_pages = int(counts_row["leased_pages"])
    worker_count = int(job_row["worker_count"])

    job_metadata = {
        "id": int(job_row["id"]),
        "origin_url": job_row["origin_url"],
        "max_depth": int(job_row["max_depth"]),
        "status": job_row["status"],
        "current_depth": current_depth,
        "created_at": job_row["created_at"],
        "started_at": job_row["started_at"],
        "finished_at": job_row["finished_at"],
        "worker_count": worker_count,
        "queue_capacity": int(job_row["queue_capacity"]),
        "request_timeout_sec": int(job_row["request_timeout_sec"]),
        "max_page_bytes": int(job_row["max_page_bytes"]),
    }

    counts = {
        "total_pages": int(counts_row["total_pages"]),
        "pages_crawled": int(counts_row["pages_crawled"]),
        "queued_pages": queued_pages,
        "leased_pages": leased_pages,
        "fetched_pages": int(counts_row["fetched_pages"]),
        "indexed_pages": int(counts_row["indexed_pages"]),
        "failed_pages": int(counts_row["failed_pages"]),
        "skipped_pages": int(counts_row["skipped_pages"]),
    }

    return {
        "job": job_metadata,
        "counts": counts,
        "queue": _build_queue_snapshot(
            db_path=db_path,
            job_id=job_id,
            current_depth=current_depth,
            total_queued=int(counts_row["queue_depth"]),
        ),
        "activity": _build_activity_snapshot(
            status=str(job_row["status"]),
            worker_count=worker_count,
            queued_pages=queued_pages,
            leased_pages=leased_pages,
        ),
        "recent_events": _get_recent_events(
            db_path=db_path,
            job_id=job_id,
            recent_event_limit=recent_event_limit,
        ),
    }


def _format_optional(value: Any) -> str:
    """Return a stable placeholder for optional values."""

    if value is None or value == "":
        return "-"
    return str(value)


def _format_yes_no(value: bool) -> str:
    """Return ``yes`` or ``no`` for boolean status fields."""

    return "yes" if value else "no"


def _format_recent_event_line(event: dict[str, Any]) -> str:
    """Render one recent-event entry as a single CLI-friendly line."""

    parts = [
        str(event["created_at"]),
        str(event["event_type"]),
    ]

    if event.get("page_url"):
        page_text = str(event["page_url"])
        if event.get("page_depth") is not None:
            page_text = f"{page_text} (depth {event['page_depth']})"
        parts.append(page_text)

    parts.append(str(event["message"]))
    return "  - " + " | ".join(parts)


def format_status_text(status_data: dict[str, Any]) -> str:
    """Format ``get_job_status(...)`` output as deterministic multiline text."""

    job = status_data["job"]
    counts = status_data["counts"]
    queue = status_data["queue"]
    activity = status_data["activity"]
    recent_events = status_data["recent_events"]

    lines = [
        f"Crawl Job {job['id']}",
        f"Status: {job['status']}",
        f"Activity: {activity['label']}",
        f"Origin URL: {job['origin_url']}",
        f"Depth: {job['current_depth']} / {job['max_depth']}",
        f"Created: {_format_optional(job['created_at'])}",
        f"Started: {_format_optional(job['started_at'])}",
        f"Finished: {_format_optional(job['finished_at'])}",
        f"Workers: {activity['active_workers']}/{activity['worker_count']} active, {activity['idle_workers']} idle",
        f"Job Running: {_format_yes_no(activity['is_running'])}",
        f"Job Idle: {_format_yes_no(activity['is_idle'])}",
        f"Queued Backlog: {_format_yes_no(activity['has_queued_pages'])}",
        f"In-flight Pages: {_format_yes_no(activity['has_inflight_pages'])}",
        "",
        "Counters",
        f"  Total pages: {counts['total_pages']}",
        f"  Pages crawled: {counts['pages_crawled']}",
        f"  Indexed: {counts['indexed_pages']}",
        f"  Fetched only: {counts['fetched_pages']}",
        f"  Queued: {counts['queued_pages']}",
        f"  Leased: {counts['leased_pages']}",
        f"  Failed: {counts['failed_pages']}",
        f"  Skipped: {counts['skipped_pages']}",
        "",
        "Queue",
        f"  Total queued: {queue['total_queued']}",
        f"  Queued at current depth: {queue['queued_at_current_depth']}",
        (
            "  Next queued depth: "
            + _format_optional(queue["next_depth_with_queued_pages"])
        ),
        f"  Queued at next depth: {queue['queued_at_next_depth']}",
        "",
        "Configuration",
        f"  Queue capacity: {job['queue_capacity']}",
        f"  Request timeout (sec): {job['request_timeout_sec']}",
        f"  Max page bytes: {job['max_page_bytes']}",
        "",
        "Recent events (newest first)",
    ]

    if recent_events:
        lines.extend(_format_recent_event_line(event) for event in recent_events)
    else:
        lines.append("  - none")

    return "\n".join(lines)


__all__ = ["format_status_text", "get_job_status"]
