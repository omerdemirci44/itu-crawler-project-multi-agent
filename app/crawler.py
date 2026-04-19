"""Sequential crawler coordinator for the MVP.

This module intentionally keeps the first crawler implementation simple:

- one page is processed at a time
- strict BFS order is enforced by depth
- fetching uses only ``urllib.request`` / ``urllib.error``
- storage integration goes through ``app.index_store`` helpers

There is no search implementation and no CLI orchestration here yet.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import socket
from typing import Any, Optional
from urllib import error, request

from . import index_store
from .parser import normalize_url, parse_html_document


_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_USER_AGENT = "itu-crawler-mvp/1.0"
_READ_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Structured result for a single fetch attempt."""

    final_url: Optional[str]
    http_status: Optional[int]
    content_type: Optional[str]
    body_text: Optional[str]
    error_reason: Optional[str]


class _ResponseTooLargeError(Exception):
    """Raised when a response body exceeds the configured byte limit."""


def _is_html_content_type(content_type: str | None) -> bool:
    """Return ``True`` when the MIME type is HTML-like for MVP parsing."""

    return (content_type or "").lower() in _HTML_CONTENT_TYPES


def _safe_exception_text(exc: BaseException) -> str:
    """Return a compact, deterministic error string for event logs and rows."""

    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _decode_body(body_bytes: bytes, charset: str | None) -> str:
    """Decode response bytes conservatively with a small fallback chain."""

    encodings: list[str] = []
    if charset:
        encodings.append(charset)
    for fallback in ("utf-8", "latin-1"):
        if fallback not in encodings:
            encodings.append(fallback)

    for encoding in encodings:
        try:
            return body_bytes.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return body_bytes.decode("utf-8", errors="replace")


def _read_limited_body(response, max_page_bytes: int) -> bytes:
    """Read a response body while enforcing the configured size limit."""

    chunks: list[bytes] = []
    total_bytes = 0

    while True:
        chunk = response.read(_READ_CHUNK_SIZE)
        if not chunk:
            break

        total_bytes += len(chunk)
        if total_bytes > max_page_bytes:
            raise _ResponseTooLargeError(f"response exceeded {max_page_bytes} bytes")

        chunks.append(chunk)

    return b"".join(chunks)


def _normalize_result_url(url: str | None, fallback_url: str | None = None) -> str | None:
    """Normalize a fetched URL, keeping the fallback when normalization fails."""

    normalized = normalize_url(url or "", base_url=None) if url else None
    if normalized is not None:
        return normalized
    return fallback_url


def _fetch_http_error(exc: error.HTTPError, original_url: str) -> FetchResult:
    """Build a fetch result for HTTP error responses."""

    header_value = exc.headers.get("Content-Type", "") if exc.headers else ""
    content_type = header_value.split(";", 1)[0].strip().lower() or None
    final_url = _normalize_result_url(exc.geturl(), normalize_url(original_url))
    return FetchResult(
        final_url=final_url,
        http_status=int(exc.code),
        content_type=content_type,
        body_text=None,
        error_reason=f"http_error:{int(exc.code)}",
    )


def _timeout_fetch_result(url: str, exc: BaseException) -> FetchResult:
    """Build a fetch result for timeout-like failures."""

    return FetchResult(
        final_url=normalize_url(url),
        http_status=None,
        content_type=None,
        body_text=None,
        error_reason=f"timeout:{_safe_exception_text(exc)}",
    )


def fetch_url(
    url: str,
    request_timeout_sec: int = 5,
    max_page_bytes: int = 1048576,
) -> FetchResult:
    """Fetch one URL with the Python standard library only.

    The fetch path is intentionally conservative:

    - only HTTP/HTTPS URLs are accepted
    - non-HTML content is skipped early
    - response bodies are capped at ``max_page_bytes``
    - decoding uses a small fallback chain and never raises to callers
    """

    normalized_url = normalize_url(url)
    if normalized_url is None:
        return FetchResult(
            final_url=None,
            http_status=None,
            content_type=None,
            body_text=None,
            error_reason="invalid_url",
        )

    request_obj = request.Request(
        normalized_url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        },
    )

    try:
        with request.urlopen(request_obj, timeout=request_timeout_sec) as response:
            http_status = int(getattr(response, "status", response.getcode()))
            final_url = _normalize_result_url(response.geturl(), normalized_url)

            # Some simple local servers omit the content type. Defaulting to
            # HTML keeps the MVP practical for local integration tests.
            header_value = response.headers.get("Content-Type", "")
            content_type = header_value.split(";", 1)[0].strip().lower() or "text/html"

            if not _is_html_content_type(content_type):
                return FetchResult(
                    final_url=final_url,
                    http_status=http_status,
                    content_type=content_type,
                    body_text=None,
                    error_reason=f"unsupported_content_type:{content_type}",
                )

            try:
                body_bytes = _read_limited_body(response, max_page_bytes)
            except _ResponseTooLargeError as exc:
                return FetchResult(
                    final_url=final_url,
                    http_status=http_status,
                    content_type=content_type,
                    body_text=None,
                    error_reason=f"response_too_large:{_safe_exception_text(exc)}",
                )

            charset = response.headers.get_content_charset()
            body_text = _decode_body(body_bytes, charset)
            return FetchResult(
                final_url=final_url,
                http_status=http_status,
                content_type=content_type,
                body_text=body_text,
                error_reason=None,
            )
    except error.HTTPError as exc:
        return _fetch_http_error(exc, normalized_url)
    except error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return _timeout_fetch_result(normalized_url, reason)
        return FetchResult(
            final_url=normalized_url,
            http_status=None,
            content_type=None,
            body_text=None,
            error_reason=f"url_error:{_safe_exception_text(reason if isinstance(reason, BaseException) else exc)}",
        )
    except (TimeoutError, socket.timeout) as exc:
        return _timeout_fetch_result(normalized_url, exc)
    except ValueError as exc:
        return FetchResult(
            final_url=normalized_url,
            http_status=None,
            content_type=None,
            body_text=None,
            error_reason=f"value_error:{_safe_exception_text(exc)}",
        )
    except OSError as exc:
        return FetchResult(
            final_url=normalized_url,
            http_status=None,
            content_type=None,
            body_text=None,
            error_reason=f"os_error:{_safe_exception_text(exc)}",
        )


def _requeue_leased_pages(db_path: str, job_id: int) -> int:
    """Reset stale leased pages so sequential crawling can resume cleanly."""

    with closing(index_store.get_connection(db_path)) as connection:
        with connection:
            cursor = connection.execute(
                """
                UPDATE page
                SET state = 'queued'
                WHERE job_id = ? AND state = 'leased'
                """,
                (job_id,),
            )
            return int(cursor.rowcount)


def _page_outcome_state(fetch_result: FetchResult) -> str:
    """Map a fetch result to the terminal page state used by storage."""

    if fetch_result.body_text is not None:
        return "indexed"
    if fetch_result.error_reason and fetch_result.error_reason.startswith(
        ("unsupported_content_type:", "response_too_large:")
    ):
        return "skipped"
    return "failed"


def _record_page_event(
    db_path: str,
    job_id: int,
    page_id: int,
    event_type: str,
    canonical_url: str,
    message: str,
) -> None:
    """Record a durable event for a page-level outcome."""

    index_store.record_crawl_event(
        db_path=db_path,
        job_id=job_id,
        page_id=page_id,
        event_type=event_type,
        message=f"{canonical_url} - {message}",
    )


def _store_child_links(
    db_path: str,
    job: dict[str, Any],
    parent_url: str,
    child_links: list[str],
    child_depth: int,
) -> None:
    """Insert discovered child pages in stable order when within depth limits."""

    if child_depth > int(job["max_depth"]):
        return

    for child_url in child_links:
        index_store.insert_discovered_page(
            db_path=db_path,
            job_id=int(job["id"]),
            canonical_url=child_url,
            origin_url=str(job["origin_url"]),
            depth=child_depth,
            parent_url=parent_url,
        )


def _process_page(db_path: str, job: dict[str, Any], page_id: int) -> None:
    """Fetch, parse, store, and finalize a single leased page."""

    page = index_store.get_page(db_path, page_id)
    fetch_result = fetch_url(
        url=str(page["canonical_url"]),
        request_timeout_sec=int(job["request_timeout_sec"]),
        max_page_bytes=int(job["max_page_bytes"]),
    )

    terminal_state = _page_outcome_state(fetch_result)
    final_url = fetch_result.final_url or str(page["canonical_url"])

    if fetch_result.body_text is None:
        index_store.update_page_state(
            db_path=db_path,
            page_id=page_id,
            state=terminal_state,
            http_status=fetch_result.http_status,
            error_reason=fetch_result.error_reason,
            final_url=final_url,
            content_type=fetch_result.content_type,
        )
        _record_page_event(
            db_path=db_path,
            job_id=int(job["id"]),
            page_id=page_id,
            event_type=f"page_{terminal_state}",
            canonical_url=str(page["canonical_url"]),
            message=fetch_result.error_reason or terminal_state,
        )
        return

    try:
        parsed_page = parse_html_document(fetch_result.body_text, final_url)
    except Exception as exc:
        error_reason = f"parse_error:{_safe_exception_text(exc)}"
        index_store.update_page_state(
            db_path=db_path,
            page_id=page_id,
            state="failed",
            http_status=fetch_result.http_status,
            error_reason=error_reason,
            final_url=final_url,
            content_type=fetch_result.content_type,
        )
        _record_page_event(
            db_path=db_path,
            job_id=int(job["id"]),
            page_id=page_id,
            event_type="page_failed",
            canonical_url=str(page["canonical_url"]),
            message=error_reason,
        )
        return

    parent_url = parsed_page.url or final_url
    next_depth = int(page["depth"]) + 1

    _store_child_links(
        db_path=db_path,
        job=job,
        parent_url=parent_url,
        child_links=parsed_page.links,
        child_depth=next_depth,
    )
    index_store.store_page_content(
        db_path=db_path,
        page_id=page_id,
        title=parsed_page.title,
        body_text=parsed_page.text,
    )
    index_store.update_page_state(
        db_path=db_path,
        page_id=page_id,
        state="indexed",
        http_status=fetch_result.http_status,
        error_reason=None,
        final_url=parent_url,
        content_type=fetch_result.content_type,
        title=parsed_page.title,
    )


def crawl_job(db_path: str, job_id: int) -> dict[str, Any]:
    """Run one crawl job to completion with strict BFS depth ordering.

    The implementation is sequential on purpose. This keeps the first version
    easy to reason about while still matching the MVP requirements around
    durability, deterministic ordering, and strict level-by-level traversal.
    """

    job = index_store.get_job(db_path, job_id)
    normalized_origin = normalize_url(str(job["origin_url"]))

    if normalized_origin is None:
        index_store.record_crawl_event(
            db_path=db_path,
            job_id=job_id,
            event_type="job_failed",
            message=f"invalid origin url: {job['origin_url']}",
        )
        index_store.complete_crawl_job(db_path, job_id, status="failed")
        return index_store.get_status_counts(db_path, job_id)

    index_store.insert_discovered_page(
        db_path=db_path,
        job_id=job_id,
        canonical_url=normalized_origin,
        origin_url=str(job["origin_url"]),
        depth=0,
        parent_url=None,
    )

    reset_count = _requeue_leased_pages(db_path, job_id)
    if reset_count:
        index_store.record_crawl_event(
            db_path=db_path,
            job_id=job_id,
            event_type="resume_requeue",
            message=f"requeued {reset_count} leased pages before crawling",
        )

    while True:
        unfinished_depths = [
            depth
            for depth in index_store.list_unfinished_depths(db_path, job_id)
            if depth <= int(job["max_depth"])
        ]
        if not unfinished_depths:
            break

        current_depth = unfinished_depths[0]
        index_store.set_job_current_depth(db_path, job_id, current_depth)

        while True:
            queued_page_ids = index_store.list_queued_page_ids_at_depth(
                db_path=db_path,
                job_id=job_id,
                depth=current_depth,
                limit=int(job["queue_capacity"]),
            )
            if not queued_page_ids:
                break

            for page_id in queued_page_ids:
                if not index_store.lease_page(db_path, page_id):
                    continue
                _process_page(db_path, job, page_id)

    counts = index_store.get_status_counts(db_path, job_id)
    final_status = (
        "completed_with_errors"
        if int(counts["failed_pages"]) > 0 or int(counts["skipped_pages"]) > 0
        else "completed"
    )
    index_store.complete_crawl_job(db_path, job_id, status=final_status)
    return index_store.get_status_counts(db_path, job_id)


__all__ = ["FetchResult", "crawl_job", "fetch_url"]
