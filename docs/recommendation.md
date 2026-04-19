# Recommendation

## 1. Recommended MVP Architecture

The MVP should be a CLI-first, single-machine system built around one shared SQLite database file and one active crawl worker process at a time.

The recommended runtime shape is:

1. A `crawl` command starts a crawl job with `origin_url` and `max_depth`.
2. A coordinator thread manages strict BFS level progression.
3. A bounded pool of fetch workers downloads pages for the current depth only.
4. Parsed page results are written back through a single database write path.
5. A `search` command can run in parallel from another process against the same SQLite database.
6. A `status` command reads counters and recent events from the same database.

For MVP, do not build a web server first. A CLI is enough and keeps the system simpler, easier to test, and more aligned with the assignment. If a local UI is added later, it should sit on top of the same database and service layer rather than changing the storage model.

Recommended implementation style:

- Language: Python
- Preferred standard library modules: `sqlite3`, `threading`, `queue`, `urllib.request`, `urllib.parse`, `html.parser`, `argparse`, `logging`, `time`, `tempfile`, `http.server`, `unittest`
- External dependencies: ideally none for MVP

## 2. Database Recommendation

Use SQLite through Python's standard-library `sqlite3` module.

This is the best MVP choice because:

- it is local, durable, and requires no separate service
- it matches the "prefer standard library" constraint
- it handles large single-machine workloads better than in-memory structures because the frontier and index can live on disk
- it is easy to inspect manually during debugging
- it supports concurrent readers with one writer when WAL mode is enabled
- it is good enough for a university-scale single-machine crawler and search system

Recommended SQLite settings:

- `PRAGMA journal_mode=WAL;`
- `PRAGMA synchronous=NORMAL;`
- `PRAGMA foreign_keys=ON;`
- `PRAGMA busy_timeout=5000;`

Recommended search indexing approach:

- Use SQLite FTS5 for full-text search if available in the local SQLite build.
- Store page metadata in normal tables and searchable text in an FTS-backed table.
- Write metadata rows and FTS rows in the same transaction so search only sees committed documents.

Why not a heavier database:

- PostgreSQL adds operational complexity and violates the "simple localhost" goal.
- A separate search engine is unnecessary for MVP.
- A custom file-based inverted index is possible, but SQLite FTS5 is simpler and more robust if available.

If FTS5 is unavailable on the target machine, the fallback should be a simple token table in SQLite, but that should be treated as a fallback path, not the primary recommendation.

## 3. Recommended Data Model

The schema should stay small and operationally clear.

### `crawl_job`

Purpose: one row per crawl run.

Recommended fields:

- `id`
- `origin_url`
- `max_depth`
- `status` (`pending`, `running`, `completed`, `completed_with_errors`, `failed`, `stopped`)
- `current_depth`
- `created_at`
- `started_at`
- `finished_at`
- `worker_count`
- `queue_capacity`
- `request_timeout_sec`
- `max_page_bytes`

### `page`

Purpose: deduplication, crawl lifecycle, and result metadata.

Recommended fields:

- `id`
- `job_id`
- `canonical_url`
- `origin_url`
- `depth`
- `parent_url`
- `state` (`queued`, `leased`, `fetched`, `indexed`, `failed`, `skipped`)
- `http_status`
- `content_type`
- `final_url`
- `title`
- `error_reason`
- `discovered_at`
- `fetched_at`
- `indexed_at`

Recommended constraint:

- `UNIQUE(job_id, canonical_url)`

This unique constraint is the practical MVP replacement for a separate `visited_url` table. It prevents the same canonical URL from being scheduled twice in the same job and is race-safe if inserts use `INSERT OR IGNORE` or equivalent conflict handling.

### `page_content`

Purpose: durable copy of extracted searchable text.

Recommended fields:

- `page_id`
- `title`
- `body_text`

Keeping extracted text in a normal table makes debugging easier and allows rebuilding search indexes if needed.

### `page_fts`

Purpose: full-text index over page title and text.

Recommended fields:

- `title`
- `body_text`
- `page_id` or implicit row linkage depending on table design

### `crawl_event`

Purpose: observable status and failure history.

Recommended fields:

- `id`
- `job_id`
- `page_id` nullable
- `event_type`
- `message`
- `created_at`

This table should record failures, skips, stop events, and resume/requeue events. It is useful both for status reporting and for debugging tests.

## 4. Module Responsibilities

The codebase should separate responsibilities around stable interfaces, not around premature abstractions.

### `crawler`

Responsibilities:

- validate crawl requests
- create a new `crawl_job`
- normalize and enqueue the origin URL at depth `0`
- enforce strict BFS level progression
- lease a bounded set of pages from the current depth
- dispatch fetch work to worker threads
- receive parsed results
- insert newly discovered child URLs at depth `d + 1`
- stop advancing when `d == max_depth`
- update job-level counters and final status

Important design note:

- `crawler` owns BFS semantics and deduplication policy.
- `crawler` should not contain HTML parsing rules or SQL details beyond calling storage interfaces.

### `parser`

Responsibilities:

- parse HTML bytes into text, title, and outgoing links
- resolve relative links against the fetched page URL
- remove fragments
- normalize scheme and host case
- optionally normalize default ports and simple trailing-slash cases
- skip unsupported content types
- enforce maximum page size and safe decode behavior

Recommended parser scope for MVP:

- handle HTML only
- ignore JavaScript execution
- ignore CSS and binary assets
- extract anchor `href` values and visible text conservatively

Use standard-library `html.parser` plus `urllib.parse` for MVP.

### `index_store`

Responsibilities:

- own all SQLite connections and schema setup
- create jobs, page rows, content rows, and event rows
- enforce unique `(job_id, canonical_url)` inserts
- provide page leasing and state transitions
- write indexed text and FTS rows in one transaction
- expose read methods for search and status
- expose a startup repair step for partial resume

Important design note:

- keep a single writer connection for crawl writes
- use separate read connections for search and status

### `search`

Responsibilities:

- validate query input
- run deterministic FTS queries over committed rows
- join search hits back to `page` and `crawl_job`
- return triples `(relevant_url, origin_url, depth)`
- apply limit and stable tie-breaking

Recommended ranking:

- primary: FTS score such as `bm25`
- tie-breakers: lower depth first, then URL ascending

The exact formula does not need to be sophisticated. It only needs to be deterministic and documented.

### `status`

Responsibilities:

- show active jobs and job states
- show pages crawled, indexed, failed, skipped
- show current depth and maximum depth
- show number of queued pages at current depth and next depth
- show whether workers are active or idle
- show recent failures from `crawl_event`

This module should stay read-only.

### `server/main`

Responsibilities:

- provide the CLI entrypoint
- load configuration
- route commands such as `crawl`, `search`, `status`, `resume`, `stop`
- initialize logging and database path
- start the crawl coordinator when requested

For MVP, `server/main` should mean "entrypoint and process orchestration", not "HTTP server".

Recommended CLI shape:

- `python -m app.main crawl --db data.db --origin http://localhost:8000 --depth 2`
- `python -m app.main search --db data.db --query "example"`
- `python -m app.main status --db data.db`
- `python -m app.main resume --db data.db --job 1`

## 5. Recommended Concurrency Model

Use threads, not `asyncio`, for the MVP crawler.

Reason:

- the workload is mostly network I/O plus light parsing
- Python threads are simpler to reason about for a small multi-agent implementation
- the standard library is enough
- database write serialization is easier with one writer thread or one write path

Recommended model:

- one coordinator thread
- one bounded work queue for fetch tasks
- `N` fetch worker threads
- one bounded result queue
- one database write path

Practical execution pattern:

1. The coordinator selects pages from `page` where `state='queued'` and `depth=current_depth`.
2. It leases only up to the configured in-flight limit.
3. Workers fetch and parse pages.
4. Results flow into the result queue.
5. The writer updates page state, stores content, inserts child URLs for `depth + 1`, and records events.
6. Only when no queued or leased pages remain for `current_depth` does the coordinator advance to the next depth.

This gives:

- strict BFS by level
- controlled memory use
- simple reasoning about correctness
- easy search/index isolation

Do not start pages at depth `d + 1` before depth `d` is finished. That is the cleanest way to satisfy the PRD's BFS requirement.

## 6. Recommended Back-Pressure Design

Back pressure should be explicit and visible.

Use three layers of protection:

### A. Disk-backed frontier

Discovered pages should go into SQLite, not into an unbounded in-memory list. The database is the durable frontier.

### B. Bounded in-memory queues

Use:

- a bounded fetch work queue
- a bounded parse/write result queue

When the result queue fills, workers block. When the work queue fills, the coordinator stops leasing new pages. This is the core back-pressure mechanism.

### C. Resource limits per page

Enforce:

- request timeout
- maximum response size
- worker count limit
- queue capacity limit
- optional per-host crawl delay if needed later

Recommended defaults for MVP:

- `worker_count`: 8
- `work_queue_capacity`: 16
- `result_queue_capacity`: 16
- `request_timeout_sec`: 5 to 10
- `max_page_bytes`: 1 to 2 MB

These are conservative and easy to tune on a developer machine.

Operational rule:

- never hold the whole next frontier in memory
- only lease a small active window from the database

That is what makes large single-machine workloads practical.

## 7. Search While Indexing

Search should work against committed snapshots only.

Recommended design:

- enable SQLite WAL mode
- use one connection for crawl writes
- use separate read-only connections for search and status
- commit page metadata, extracted text, and FTS rows in one short transaction

Why this is safe:

- SQLite readers in WAL mode do not block on ordinary writes
- readers see only committed transactions
- partially indexed pages are invisible until commit

Recommended indexing rule:

- a page becomes searchable only after both metadata and text index rows are committed

Recommended search query path:

1. query `page_fts`
2. join matching rows to `page`
3. filter to indexed pages only
4. return `(canonical_url, origin_url, depth)` as `(relevant_url, origin_url, depth)`

This keeps search available during active indexing without requiring a separate search service.

## 8. Resume Recommendation

Recommend partial resume for MVP, not full resume.

Full resume is not worth the complexity for the first version because it requires restoring exact in-flight worker state, network attempts, and transient queue contents.

Partial resume is the practical middle ground:

- completed indexed pages stay searchable
- queued pages remain queued
- pages left in `leased` state after interruption are reset to `queued` on restart
- failed and skipped pages remain recorded as-is
- the job restarts from persisted database state, not from the exact in-memory state

Recommended startup repair step:

- on `resume`, move all pages in transient states such as `leased` back to `queued`
- recompute the smallest unfinished depth and set `crawl_job.current_depth` to that value

This gives useful restart behavior with low implementation risk.

If time is limited, partial resume should come after core crawl correctness, search correctness, and back pressure.

## 9. Testing Approach for Localhost

Testing should be localhost-only and deterministic.

Use standard-library tools first:

- `unittest`
- `tempfile`
- `sqlite3`
- `http.server` or `ThreadingHTTPServer`

Recommended test layers:

### A. Unit tests

Cover:

- URL normalization
- fragment removal
- relative link resolution
- depth assignment
- duplicate insert handling
- query validation

### B. Integration tests with a synthetic local site

Create a small local HTTP graph such as:

- `/a` links to `/b` and `/c`
- `/b` links to `/d`
- `/c` links to `/d`
- `/d` links back to `/a`

Use it to verify:

- origin depth is `0`
- BFS ordering by depth
- no duplicate crawling of `/d`
- correct stop at depth `k`
- correct `(relevant_url, origin_url, depth)` output

### C. Concurrency and incremental search tests

Use pages with intentional response delays to simulate a long-running crawl.

Verify:

- search works while crawl is active
- pages become searchable only after commit
- queue sizes remain bounded
- status reflects active progress

### D. Restart tests

Start a crawl, stop it after some pages commit, then reopen the database.

Verify:

- committed pages are still searchable
- partial resume requeues transient work safely
- database remains readable without manual repair

### E. Failure-path tests

Simulate:

- timeout
- invalid HTML
- non-HTML content
- oversize response
- broken link

Verify:

- the job continues
- failures are recorded
- search still works for already indexed pages

## 10. What Should Go Into `docs/recommendation.md`

The recommendation document should contain:

1. the concrete MVP architecture and why it was chosen
2. the exact database recommendation and SQLite settings
3. the schema outline and deduplication strategy
4. module boundaries and responsibilities
5. the concurrency model and BFS enforcement rule
6. the back-pressure design and bounded-resource rules
7. the search-while-indexing design and transaction rule
8. the resume decision for MVP
9. the localhost testing strategy
10. a short list of explicit non-recommendations for MVP

## 11. Explicit Non-Recommendations for MVP

Do not do these in the first version:

- no distributed crawling
- no browser automation
- no JavaScript rendering
- no separate web server requirement
- no external search engine
- no multiple concurrent crawl jobs writing heavily at once
- no full-fidelity resume of in-flight worker state

## 12. Final Recommendation

Build the MVP as a CLI-first Python application backed by SQLite in WAL mode, with strict level-by-level BFS, a bounded thread pool for fetch/parse work, a single durable write path, and SQLite FTS-based search over committed content.

This is the simplest design that satisfies the assignment's real constraints:

- correct BFS semantics
- no duplicate crawl per job
- large workload support on one machine
- explicit back pressure
- concurrent search during indexing
- minimal dependencies
- straightforward testing on localhost
