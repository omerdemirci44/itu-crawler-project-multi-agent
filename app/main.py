"""CLI entrypoint for the crawler/search MVP.

This module intentionally stays small and standard-library only. It wires the
existing crawler, search, status, server, and storage helpers into a practical
command-line interface.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .crawler import crawl_job
from . import index_store
from .parser import normalize_url
from .search import search_query
from .server import run_server
from .status import format_status_text, get_job_status


EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_USER_ERROR = 2


def _positive_int(value: str) -> int:
    """Parse a positive integer for command-line arguments."""

    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def _non_negative_int(value: str) -> int:
    """Parse a non-negative integer for command-line arguments."""

    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc

    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    """Create the top-level CLI parser with MVP subcommands."""

    parser = argparse.ArgumentParser(
        prog="python -m app.main",
        description="Crawler/search MVP CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl_parser = subparsers.add_parser("crawl", help="start and run a crawl job")
    crawl_parser.add_argument("--db", required=True, help="path to the SQLite database")
    crawl_parser.add_argument("--origin", required=True, help="origin URL to crawl")
    crawl_parser.add_argument(
        "--depth",
        required=True,
        type=_non_negative_int,
        help="maximum crawl depth (>= 0)",
    )
    crawl_parser.set_defaults(handler=_handle_crawl)

    search_parser = subparsers.add_parser("search", help="search indexed pages")
    search_parser.add_argument("--db", required=True, help="path to the SQLite database")
    search_parser.add_argument("--query", required=True, help="search query text")
    search_parser.add_argument(
        "--limit",
        default=10,
        type=_positive_int,
        help="maximum number of results to print (default: 10)",
    )
    search_parser.set_defaults(handler=_handle_search)

    status_parser = subparsers.add_parser("status", help="show crawl job status")
    status_parser.add_argument("--db", required=True, help="path to the SQLite database")
    status_parser.add_argument(
        "--job",
        required=True,
        type=_positive_int,
        help="crawl job id",
    )
    status_parser.set_defaults(handler=_handle_status)

    serve_parser = subparsers.add_parser("serve", help="run the localhost web UI")
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host interface to bind (default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        default=8000,
        type=_positive_int,
        help="port to bind (default: 8000)",
    )
    serve_parser.set_defaults(handler=_handle_serve)

    return parser


def _handle_crawl(args: argparse.Namespace) -> int:
    """Initialize storage, run one crawl job, and print a short summary."""

    normalized_origin = normalize_url(args.origin)
    if normalized_origin is None:
        raise ValueError("origin must be a valid http or https URL")

    index_store.initialize_schema(args.db)

    job_id = index_store.create_crawl_job(
        db_path=args.db,
        origin_url=args.origin,
        max_depth=args.depth,
    )

    # Pre-insert the origin so the job has a durable starting page before the
    # crawler begins. `crawl_job(...)` safely deduplicates this row if repeated.
    index_store.insert_discovered_page(
        db_path=args.db,
        job_id=job_id,
        canonical_url=normalized_origin,
        origin_url=args.origin,
        depth=0,
        parent_url=None,
    )

    counts = crawl_job(args.db, job_id)
    final_job = index_store.get_job(args.db, job_id)

    print(
        "crawl completed: "
        f"job={job_id} "
        f"status={final_job['status']} "
        f"pages={counts['total_pages']} "
        f"indexed={counts['indexed_pages']} "
        f"failed={counts['failed_pages']} "
        f"skipped={counts['skipped_pages']}"
    )
    return EXIT_SUCCESS


def _handle_search(args: argparse.Namespace) -> int:
    """Run a query and print result triples one per line."""

    results = search_query(
        db_path=args.db,
        query=args.query,
        limit=args.limit,
    )

    if not results:
        print("no results")
        return EXIT_SUCCESS

    for relevant_url, origin_url, depth in results:
        print(f"({relevant_url}, {origin_url}, {depth})")

    return EXIT_SUCCESS


def _handle_status(args: argparse.Namespace) -> int:
    """Print formatted status text for one crawl job."""

    status_data = get_job_status(
        db_path=args.db,
        job_id=args.job,
    )
    print(format_status_text(status_data))
    return EXIT_SUCCESS


def _handle_serve(args: argparse.Namespace) -> int:
    """Run the localhost web UI until interrupted."""

    run_server(
        host=args.host,
        port=args.port,
    )
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments, dispatch the selected command, and return an exit code."""

    parser = _build_parser()

    try:
        args = parser.parse_args(argv)
        return int(args.handler(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
