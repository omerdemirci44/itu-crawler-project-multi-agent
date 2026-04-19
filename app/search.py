"""SQLite-backed search helpers for the crawler/search MVP.

This module intentionally implements a small lexical fallback search path
using ordinary SQLite tables. The recommended FTS table does not exist yet,
so the MVP uses case-insensitive substring matching over committed content in
`page_content` and joins back to `page` for crawl metadata.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from app.index_store import get_connection, initialize_schema


def _normalize_query(query: str) -> tuple[str, tuple[str, ...]]:
    """Return a normalized query string and its distinct whitespace terms."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty, non-whitespace string")

    # Collapse repeated whitespace so phrase matching is predictable.
    normalized_query = " ".join(query.split()).lower()

    # Count each distinct term at most once so repeated words in the query do
    # not artificially inflate the score.
    terms = tuple(dict.fromkeys(normalized_query.split(" ")))
    return normalized_query, terms


def _escape_like(value: str) -> str:
    """Escape special characters so LIKE treats user input literally."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_query(
    db_path: str | Path,
    query: str,
    limit: int = 20,
) -> list[tuple[str, str, int]]:
    """Search indexed pages and return `(relevant_url, origin_url, depth)`.

    Ranking is intentionally simple and deterministic for the MVP:

    1. full-query phrase match in the title
    2. count of distinct query terms matched in the title
    3. full-query phrase match in the body text
    4. count of distinct query terms matched in the body text
    5. lower crawl depth first
    6. canonical URL ascending
    7. origin URL, job id, and page id ascending for stable final tie-breaking

    Only pages already marked `indexed` are eligible. This keeps search
    consistent while indexing is active because readers only see committed rows.
    """

    normalized_query, terms = _normalize_query(query)

    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    initialize_schema(db_path)

    phrase_pattern = f"%{_escape_like(normalized_query)}%"
    parameters: dict[str, object] = {
        "phrase": phrase_pattern,
        "limit": limit,
    }

    title_term_clauses: list[str] = []
    body_term_clauses: list[str] = []
    term_match_filters: list[str] = []

    for index, term in enumerate(terms):
        parameter_name = f"term_{index}"
        parameters[parameter_name] = f"%{_escape_like(term)}%"

        title_term_clauses.append(
            f"CASE WHEN title_text LIKE :{parameter_name} ESCAPE '\\' THEN 1 ELSE 0 END"
        )
        body_term_clauses.append(
            f"CASE WHEN body_text LIKE :{parameter_name} ESCAPE '\\' THEN 1 ELSE 0 END"
        )
        term_match_filters.append(f"title_text LIKE :{parameter_name} ESCAPE '\\'")
        term_match_filters.append(f"body_text LIKE :{parameter_name} ESCAPE '\\'")

    title_term_score = " + ".join(title_term_clauses) if title_term_clauses else "0"
    body_term_score = " + ".join(body_term_clauses) if body_term_clauses else "0"

    sql = f"""
        WITH searchable_pages AS (
            SELECT
                p.id AS page_id,
                p.job_id,
                p.canonical_url AS relevant_url,
                p.origin_url,
                p.depth,
                LOWER(COALESCE(pc.title, '')) AS title_text,
                LOWER(pc.body_text) AS body_text
            FROM page AS p
            INNER JOIN page_content AS pc
                ON pc.page_id = p.id
            WHERE p.state = 'indexed'
        )
        SELECT
            relevant_url,
            origin_url,
            depth
        FROM searchable_pages
        WHERE
            title_text LIKE :phrase ESCAPE '\\'
            OR body_text LIKE :phrase ESCAPE '\\'
            OR {" OR ".join(term_match_filters)}
        ORDER BY
            CASE WHEN title_text LIKE :phrase ESCAPE '\\' THEN 1 ELSE 0 END DESC,
            ({title_term_score}) DESC,
            CASE WHEN body_text LIKE :phrase ESCAPE '\\' THEN 1 ELSE 0 END DESC,
            ({body_term_score}) DESC,
            depth ASC,
            relevant_url ASC,
            origin_url ASC,
            job_id ASC,
            page_id ASC
        LIMIT :limit
    """

    with closing(get_connection(db_path)) as connection:
        rows = connection.execute(sql, parameters).fetchall()

    return [
        (str(row["relevant_url"]), str(row["origin_url"]), int(row["depth"]))
        for row in rows
    ]


__all__ = ["search_query"]
