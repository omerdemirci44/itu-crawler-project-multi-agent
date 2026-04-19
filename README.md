# Localhost Web Crawler and Search MVP

This repository contains a Python crawler and search system built for a university assignment. It crawls a localhost-accessible website from an origin URL, stores crawl state durably in SQLite, indexes extracted HTML text, and supports search and status inspection through both a small command-line interface and a lightweight localhost web UI.

The runtime system is a normal Python application. The multi-agent aspect of the project applies to the development workflow and documentation process, not to the production runtime.

## What The System Does

The system starts from an origin URL and crawls reachable pages up to a maximum BFS depth `k`.

For each crawl job, it:

- normalizes and deduplicates URLs
- fetches HTML pages with the Python standard library
- extracts visible text, title, and outgoing links
- stores crawl jobs, pages, page content, and events in SQLite
- marks pages as indexed, skipped, or failed
- lets users inspect crawl status and query indexed content

Search results are returned in the required form:

```text
(relevant_url, origin_url, depth)
```

## Why It Was Built With A Multi-Agent Workflow

The assignment required a multi-agent workflow. This repository satisfied that requirement during development by splitting the work into focused roles:

- PRD definition
- architecture and storage design
- crawler and parser implementation
- search implementation
- operator-facing status/reporting
- review and integration

That workflow is documented in [docs/multi_agent_workflow.md](docs/multi_agent_workflow.md) and reflected in the agent responsibility files under [agents](agents/).

Important clarification:

- The shipped application is not a multi-agent runtime.
- When you run the project, it is a standard Python CLI program.
- The multi-agent aspect belongs to how the repository was designed, reviewed, and integrated.

## Key Features

- CLI and localhost web UI entrypoints through `crawl`, `search`, `status`, and `serve`
- SQLite-backed durable crawl state using the Python standard library
- URL normalization and per-job deduplication via `UNIQUE(job_id, canonical_url)`
- Strict level-by-level BFS traversal in the current implementation
- Deterministic lexical search over committed indexed content
- Lightweight localhost web UI built on `http.server` with form-based crawl, search, and status views
- Background crawl execution from the web UI using in-process daemon threads
- Read-only status reporting with counters, queue information, activity labels, and recent crawl events
- Practical handling of unsupported content types, oversized responses, HTTP errors, and invalid URLs
- Deterministic end-to-end integration testing with a small local HTTP fixture site

## Architecture Overview

The MVP is intentionally simple and modular. It supports both direct CLI usage and a small localhost web UI on top of the same crawler, search, status, and storage modules.

### Runtime flow

1. A crawl job is created with an origin URL and maximum depth.
2. The crawler processes queued pages in ascending depth order.
3. Each fetched HTML page is parsed into title, visible text, and discovered links.
4. New child pages are inserted into the durable frontier only when their depth is within the configured limit.
5. Indexed content is stored in SQLite.
6. Search and status requests can be made from either the CLI or the localhost web UI.
7. When the web UI starts a crawl, the server launches the crawl job in a background thread and continues serving search and status pages.

### Main modules

- [app/main.py](app/main.py): CLI entrypoint and command wiring
- [app/index_store.py](app/index_store.py): SQLite schema, storage helpers, lifecycle operations
- [app/parser.py](app/parser.py): URL normalization and HTML parsing
- [app/crawler.py](app/crawler.py): fetch logic and sequential BFS crawl coordination
- [app/search.py](app/search.py): query validation, ranking, and result retrieval
- [app/server.py](app/server.py): lightweight localhost web UI and background crawl launching
- [app/status.py](app/status.py): read-only status snapshot and text formatting

### Storage model

The SQLite database contains, at minimum, these logical entities:

- `crawl_job`
- `page`
- `page_content`
- `crawl_event`

This keeps crawl state durable and inspectable while remaining simple to run locally.

## Repository Structure

```text
.
|-- agents/
|   |-- crawler_agent.md
|   |-- critic_agent.md
|   |-- infra_agent.md
|   |-- prd_agent.md
|   |-- search_agent.md
|   `-- ui_agent.md
|-- app/
|   |-- crawler.py
|   |-- index_store.py
|   |-- main.py
|   |-- parser.py
|   |-- search.py
|   |-- server.py
|   `-- status.py
|-- docs/
|   |-- multi_agent_workflow.md
|   |-- product_prd.md
|   `-- recommendation.md
|-- tests/
|   `-- test_end_to_end.py
`-- README.md
```

## Requirements And Environment

### Runtime requirements

- Python 3.10 or newer
- No external Python packages required
- Local filesystem write access for the SQLite database
- Localhost HTTP access for crawling test sites or locally served pages

### Standard-library modules used

The implementation intentionally relies on the Python standard library, including:

- `argparse`
- `sqlite3`
- `threading`
- `urllib.request`
- `urllib.parse`
- `html.parser`
- `http.server`
- `unittest`

## How To Run Locally

From the repository root:

```bash
python -m app.main --help
```

Create a database file anywhere you want inside the repository, for example under `data/`.

### Start the localhost web UI

```bash
python -m app.main serve --host 127.0.0.1 --port 8000
```

### Start a crawl

```bash
python -m app.main crawl --db data/crawler.db --origin http://localhost:8000/index.html --depth 2
```

### Search indexed content

```bash
python -m app.main search --db data/crawler.db --query "crawler" --limit 10
```

### Show job status

```bash
python -m app.main status --db data/crawler.db --job 1
```

## Example Commands

### `crawl`

```bash
python -m app.main crawl \
  --db data/crawler.db \
  --origin http://localhost:8000/index.html \
  --depth 2
```

Typical output:

```text
crawl completed: job=1 status=completed pages=4 indexed=4 failed=0 skipped=0
```

If the crawl encounters skipped or failed pages, the final status may be `completed_with_errors`.

### `serve`

```bash
python -m app.main serve \
  --host 127.0.0.1 \
  --port 8000
```

Typical output:

```text
Serving crawler UI at http://127.0.0.1:8000/
```

The web UI provides small form-based pages for starting background crawl jobs, running searches, and viewing status.

### `search`

```bash
python -m app.main search \
  --db data/crawler.db \
  --query "crawler" \
  --limit 10
```

Typical output:

```text
(http://localhost:8000/about.html, http://localhost:8000/index.html, 1)
(http://localhost:8000/guide.html, http://localhost:8000/index.html, 2)
```

### `status`

```bash
python -m app.main status \
  --db data/crawler.db \
  --job 1
```

Typical output includes:

- job id and status
- origin URL
- current depth versus max depth
- page counters
- queue information
- active or idle activity label
- recent crawl events

## Testing

Run the integration test:

```bash
python -m unittest tests.test_end_to_end -v
```

The test:

- starts a tiny local HTTP site in-process
- creates a temporary SQLite database under the workspace
- runs the real crawl flow through storage and crawler modules
- verifies practical BFS behavior, deduplication, search, status formatting, and non-HTML skipping

You can also run a quick syntax check on the main entrypoint and the integration test:

```bash
python -m py_compile app/main.py tests/test_end_to_end.py
```

## Current Limitations

This is an MVP and intentionally conservative.

- The crawler is sequential in the shipped implementation, not a threaded worker pool.
- The localhost web UI is intentionally minimal and form-based, not a full-featured browser application.
- The crawler is focused on HTML content and does not execute JavaScript.
- Search uses a deterministic lexical SQLite query over stored content, not FTS5 or semantic retrieval.
- Background crawl threads started by the web UI are tracked only within the running server process.
- The current CLI does not implement resume or stop commands.
- Search and status can operate against the same database, but the crawl command itself runs synchronously in the invoking process until completion.
- The web UI is localhost-oriented and does not include authentication, multi-user controls, or production hardening.
- Binary assets and advanced content extraction are out of scope for the MVP.

## How Search Can Work While Indexing Is Active

The design supports concurrent reading from the same SQLite database while a crawl is writing committed updates.

In practical terms:

- crawl writes go through SQLite using WAL mode
- search and status open their own read connections
- search only returns rows where `page.state = 'indexed'`
- readers only observe committed data, not partially written page content

That means a crawl can run in one process or terminal, or in a background thread started by the web UI, while `search` or `status` is run against the same database from the CLI or the UI. Newly committed pages become searchable as soon as the crawler finishes writing them.

The current implementation keeps the crawl loop simple and sequential, but the storage and read-path design still allow separate read commands against committed state.

## Future Improvements

Reasonable next steps for this project would be:

- add resume and stop commands to the CLI
- upgrade search to SQLite FTS5 when available
- add bounded concurrent fetching while preserving strict BFS-by-depth semantics
- support richer crawl events and more detailed diagnostics
- improve the localhost web UI beyond the current minimal form-based interface
- extend parser behavior for more content types where justified
- improve test coverage for failure paths, interruption handling, and larger crawl graphs

## Deliverables Included In The Repo

This repository includes the main assignment deliverables:

- [README.md](README.md): repository overview and run instructions
- [docs/product_prd.md](docs/product_prd.md): product requirements and acceptance criteria
- [docs/recommendation.md](docs/recommendation.md): architecture and technical recommendation
- [docs/multi_agent_workflow.md](docs/multi_agent_workflow.md): explanation of the development workflow
- [agents/](agents/): agent responsibility documents used in the workflow
- [app/](app/): implementation code
- [tests/test_end_to_end.py](tests/test_end_to_end.py): deterministic end-to-end integration test

## Submission Note

This repository is intended to be submission-ready as a localhost crawler/search MVP:

- requirements are documented
- architecture decisions are documented
- the implementation is modular and runnable
- the operator interface is available through both the CLI and a simple localhost web UI
- end-to-end integration is covered by a deterministic test

The final runtime remains a conventional Python application, while the multi-agent requirement is satisfied by the documented development process used to produce the repository.
