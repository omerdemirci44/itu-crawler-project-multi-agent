# Product PRD

## 1. Document Purpose

This document defines the product requirements for a Python-based localhost web crawler and search system for a university assignment. It is intended to be the source of truth for later implementation by other AI agents and should be read as a practical build specification, not as a research note.

The system must:

- run on localhost
- persist data in a local database
- use Python language-native functionality as much as possible
- crawl from an origin URL using breadth-first search (BFS) up to depth `k`
- never crawl the same page twice within the same crawl job
- scale to large workloads on a single machine
- include back pressure so crawl work stays bounded
- allow search to run while indexing is active
- return search results as triples: `(relevant_url, origin_url, depth)`
- provide a simple CLI or UI to start indexing, search, and inspect state

Resume after interruption is desirable but not mandatory for the first working version.

## 2. Product Summary

Build a local system that starts from a user-provided origin URL, crawls reachable pages in BFS order up to a maximum depth, extracts and indexes searchable text, and serves search queries against the indexed corpus while crawling is still in progress.

The product should favor operational simplicity over feature breadth. The assignment is not to build a distributed search engine. The goal is a correct, inspectable, durable, single-machine crawler/search tool with clear tradeoffs and stable behavior under load.

## 3. Goals

- Provide correct BFS crawling semantics with explicit depth tracking.
- Persist crawl state and indexed content in a local database.
- Prevent duplicate crawling of the same canonical URL within a crawl job.
- Support incremental search while indexing is active.
- Expose a minimal operator interface for crawl start, search, and status inspection.
- Stay stable on a single machine through bounded queues, bounded concurrency, and disk-backed state.
- Keep the design understandable enough that separate AI agents can implement components independently.

## 4. Non-Goals

- Distributed crawling or multi-machine coordination
- Full browser rendering of JavaScript-heavy websites
- Advanced semantic retrieval or LLM-based ranking
- Multi-user authentication and permissions
- Internet-scale freshness guarantees
- Full web archiving of binary assets

## 5. Users and Core Workflows

### Primary User

The primary user is a developer, student, or evaluator running the system locally.

### Core Workflows

1. Start a crawl by providing an origin URL and maximum depth `k`.
2. Observe crawl progress, queue pressure, failures, and current depth.
3. Run search queries while crawling continues in the background.
4. Inspect whether new pages are becoming searchable over time.
5. Stop the process cleanly, and optionally resume later if resume support exists.

## 6. Product Scope and Outline

The PRD covers the following product areas:

1. Crawl job management
2. URL normalization and deduplication
3. BFS frontier management
4. Fetching and HTML text extraction
5. Local persistence and indexing
6. Incremental search
7. Back pressure and single-machine resource control
8. CLI or simple UI for operations
9. Error handling, monitoring, and optional resume

## 7. Key Entities

The implementation should preserve at least the following logical entities, even if names differ:

- `crawl_job`: identifies one indexing run and stores `origin_url`, `max_depth`, status, timestamps, and configuration.
- `page`: stores canonical URL, origin URL, assigned depth, fetch status, content metadata, and indexing state.
- `frontier_item`: represents pending crawl work with depth and job association.
- `visited_url`: records canonical URLs already scheduled or crawled for a given job to prevent duplicates.
- `search_document`: stores searchable text and metadata needed to return `(relevant_url, origin_url, depth)`.
- `crawl_event` or equivalent status/log data: stores failures, skips, retries, and operational counters.

## 8. Functional Requirements

| ID | Priority | Requirement |
|---|---|---|
| FR-1 | Must | The system must allow a user to start a new crawl job by supplying an `origin_url` and maximum depth `k`. |
| FR-2 | Must | The system must assign the origin page depth `0` and crawl reachable pages in BFS order up to and including depth `k`. |
| FR-3 | Must | The system must never crawl the same canonical URL twice within the same crawl job. Deduplication must happen before enqueue and be protected against race conditions. |
| FR-4 | Must | The system must normalize URLs before deduplication. At minimum it must resolve relative links, remove fragments, and normalize host/scheme case consistently. |
| FR-5 | Must | The system must store crawl metadata and searchable content in a local database. In-memory-only indexing is not acceptable. |
| FR-6 | Must | The system must extract links from successfully fetched HTML pages and add newly discovered candidates to the BFS frontier only when their computed depth is `<= k`. |
| FR-7 | Must | The system must store enough metadata to return search results as triples in the form `(relevant_url, origin_url, depth)`. |
| FR-8 | Must | The system must accept a query string and return results ordered by relevance. The exact ranking algorithm may be simple, but it must be deterministic and documented. |
| FR-9 | Must | Search must be usable while indexing is active and must reflect newly committed index data incrementally without restarting the process. |
| FR-10 | Must | The system must provide a simple operator interface, preferably CLI-first, to start indexing, run search, and inspect system state. |
| FR-11 | Must | The operator interface must show at least: current job status, origin URL, max depth, pages crawled, pages indexed, failed pages, skipped pages, queue depth, and whether indexing is active or idle. |
| FR-12 | Must | The crawler must apply back pressure through a bounded queue, bounded worker pool, rate limiting, or an equivalent mechanism that prevents unbounded growth of pending work. |
| FR-13 | Must | The crawler must persist crawl progress incrementally so a crash or interruption does not corrupt the database. |
| FR-14 | Should | The system should support clean shutdown of an active crawl so all committed data remains searchable. |
| FR-15 | Should | The system should support resuming an interrupted crawl job from previously persisted state rather than starting from scratch. |
| FR-16 | Should | The system should expose configuration for worker count, queue capacity, request timeout, maximum page size, crawl delay or rate limit, and database path. |
| FR-17 | Should | The system should record error reasons for failed or skipped pages, such as timeout, unsupported content type, redirect loop, parse failure, or HTTP error. |
| FR-18 | Should | The search command should support a result limit parameter with a sensible default. |
| FR-19 | Could | The system could provide a lightweight localhost web UI in addition to the CLI, but the CLI is sufficient for MVP. |

### Functional Notes

- For MVP, a lexical full-text search approach is sufficient. A page is considered relevant if the query matches indexed text or title according to the documented ranking method.
- If concurrency is used, strict BFS semantics must still hold. Parallelism is acceptable within the same depth level, but pages at depth `d + 1` must not be started before all eligible pages at depth `d` have been processed or explicitly skipped.
- Search may expose optional fields such as title, score, or snippet in the UI, but the canonical required output remains the triple `(relevant_url, origin_url, depth)`.
- The schema should preserve `origin_url` even if MVP only supports one active crawl job at a time.

## 9. Non-Functional Requirements

| ID | Priority | Requirement |
|---|---|---|
| NFR-1 | Must | The system must run fully on localhost with no required cloud service or remote database dependency. |
| NFR-2 | Must | The system must use a local database and rely on Python standard-library functionality where practical. External dependencies should be minimal and justified. |
| NFR-3 | Must | The design must support large crawl workloads on a single machine by keeping durable state on disk rather than requiring the full corpus in memory. |
| NFR-4 | Must | Memory growth must remain bounded by configured limits such as queue size, worker count, and batch size. |
| NFR-5 | Must | Search must be concurrency-safe with indexing. A query must observe committed data only and must not depend on partially written index records. |
| NFR-6 | Must | The system must remain responsive enough that a user can issue search and status commands while indexing is active. |
| NFR-7 | Must | Failures in fetching individual pages must not terminate the entire crawl job unless the database or core runtime becomes unusable. |
| NFR-8 | Must | The codebase must be modular enough that crawler, parser, index storage, search, and status/entrypoint logic can be developed and tested independently. |
| NFR-9 | Should | The system should produce logs or status output that make crawl progress and failures diagnosable without attaching a debugger. |
| NFR-10 | Should | The system should be testable with a local synthetic website or local HTTP test server so BFS order, deduplication, and incremental search can be verified deterministically. |
| NFR-11 | Should | Warm search latency should be near-interactive for moderate local datasets. A practical target is sub-second responses for common queries on a developer machine. |
| NFR-12 | Should | The system should be restart-safe. Reopening an existing database after interruption should not require manual repair. |

## 10. Assumptions

- Python is the implementation language.
- A local relational database such as SQLite is acceptable and preferred for MVP because it satisfies the local-database and minimal-dependency constraints.
- The system runs on a single machine and is operated by one local user at a time.
- The crawler is primarily intended for HTML text content over HTTP or HTTPS.
- Binary files, authenticated sessions, browser automation, and JavaScript-rendered pages are not required for MVP.
- Query understanding can be simple keyword-based search for the first version.
- The project may start with a CLI as the only interface. A local web UI is optional.
- URL normalization rules will be documented and consistently applied; canonicalization policy is part of the product behavior, not an implementation detail.
- Resume support is desirable but not required to declare MVP complete.

## 11. Edge Cases

| Case | Expected Behavior |
|---|---|
| Invalid or malformed origin URL | Reject the crawl request with a clear error before any work is scheduled. |
| `k = 0` | Only the origin page is fetched and indexed if available. No outgoing links are crawled. |
| Origin URL unreachable | Mark the job as failed or completed-with-errors, preserve the error reason, and keep the database consistent. |
| Same page discovered through multiple paths | Keep the smallest discovered depth for that job and do not crawl the page more than once. |
| Same URL differing only by fragment | Treat as the same canonical page and do not duplicate crawl work. |
| Relative links, trailing slashes, host case differences, default ports | Normalize consistently before deduplication. |
| Redirects | Follow according to documented policy, avoid redirect loops, and persist the final canonical URL used for indexing. |
| Self-links and cyclic link graphs | Do not loop forever; visited tracking must stop repeated scheduling. |
| Pages beyond depth `k` | Do not enqueue or crawl them. |
| Non-HTML or unsupported content type | Record as skipped or unsupported, and do not attempt text indexing unless explicitly supported. |
| Oversized page or response body | Abort according to configured size limits, record the skip or failure reason, and continue the crawl. |
| Timeout, connection reset, DNS failure, or HTTP error | Record the failure and continue processing other frontier items. |
| Duplicate enqueue caused by concurrency | Prevent with durable uniqueness checks or equivalent race-safe logic. |
| Empty query or whitespace-only query | Reject with a clear validation error rather than returning meaningless results. |
| Query with no matches | Return an empty result set cleanly. |
| Search during heavy write activity | Search remains available and returns only committed records. |
| Process interrupted mid-crawl | Database remains readable; committed results remain searchable; resume is used if implemented. |
| Disk pressure or database write failure | Surface a hard error clearly, stop unsafe writes, and avoid silent corruption. |
| Page with no extractable text but valid links | Outgoing links may still be discovered if parsing succeeds; the page may be indexed with minimal searchable content or marked accordingly. |
| Character encoding issues | Attempt reasonable decoding; if parsing fails, record the error and continue. |

## 12. Acceptance Criteria

### Core Behavior

1. A user can start a crawl job locally with an origin URL and depth `k`.
2. The system assigns depths correctly, with the origin at depth `0`.
3. On a controlled test graph, crawl order is BFS by depth, not DFS or arbitrary order.
4. No canonical URL is crawled more than once within the same crawl job, even if discovered from multiple parents.
5. The system never crawls pages whose assigned depth is greater than `k`.

### Search Behavior

1. A user can submit a query while indexing is active.
2. Search returns results as triples `(relevant_url, origin_url, depth)`.
3. Returned `depth` matches the stored crawl depth for the relevant URL under that origin.
4. Newly indexed pages become searchable without restarting the application.
5. Empty queries are rejected cleanly, and unmatched queries return an empty list without error.

### Persistence and Robustness

1. Crawl state and indexed data are stored in a local database.
2. If the process stops after some pages are committed, previously committed search results remain available after restart.
3. The crawler records failures and skips without crashing the entire job on ordinary page-level errors.
4. The system exposes enough status information to distinguish active progress from a stalled or blocked crawl.

### Back Pressure and Scale

1. Pending crawl work is bounded by configuration rather than growing without limit.
2. On a larger controlled dataset, the system continues operating without unbounded memory growth.
3. Operator-visible status shows queue depth or equivalent pressure indicators during heavy crawl activity.

### Interface

1. The user can start indexing from the CLI or simple UI.
2. The user can run search from the CLI or simple UI.
3. The user can inspect system state from the CLI or simple UI.

### Deliverable Completion

1. `README.md` explains how to run the system locally.
2. `docs/product_prd.md` reflects this requirements baseline.
3. `docs/recommendation.md` documents key architectural and technical recommendations.
4. `docs/multi_agent_workflow.md` describes how work is divided among agents.
5. `agents/*.md` define clear responsibilities for the participating agents.

## 13. Recommended Milestones

| Milestone | Outcome | Exit Criteria |
|---|---|---|
| M1. Requirements Freeze | Align all agents on the same product definition. | PRD reviewed, major ambiguities resolved, scope and non-goals agreed. |
| M2. Data Model and Storage | Establish durable local storage for jobs, pages, frontier, and index data. | Database schema exists, lifecycle states are defined, and persistence strategy is documented. |
| M3. BFS Crawler Core | Implement crawl job creation, BFS frontier handling, URL normalization, deduplication, fetch, and parse flow. | Controlled crawl works end-to-end for a local test site and respects depth limits and no-duplicate rules. |
| M4. Search and Incremental Visibility | Implement searchable index and query path over committed data. | Queries return `(relevant_url, origin_url, depth)` and results appear during active indexing. |
| M5. Operator Interface and Status | Provide a usable CLI or simple UI for start, search, and status. | User can operate the system without touching internal modules or the database directly. |
| M6. Hardening and Resource Control | Add back pressure, concurrency controls, failure handling, and optional resume. | Queue growth is bounded, failures are visible, and interruption handling is stable. |
| M7. Documentation and Agent Handoff | Finalize all assignment deliverables. | README, recommendation, workflow, and agent docs are consistent with the implemented system. |

## 14. Recommended Delivery Order for AI Agents

To reduce rework in a multi-agent workflow, agents should generally proceed in this order:

1. PRD agent finalizes requirements and unresolved decisions.
2. Recommendation or architecture agent chooses concrete technical direction within the PRD constraints.
3. Infrastructure and storage agent defines database schema, configuration, and process lifecycle.
4. Crawler agent implements BFS crawl and deduplication behavior.
5. Search agent implements indexing and query behavior over committed data.
6. UI or CLI agent provides the operator interface and status views.
7. Critic or review agent validates acceptance criteria, edge cases, and documentation consistency.

## 15. Open Decisions to Resolve Early

These decisions should be resolved in `docs/recommendation.md` before implementation expands:

- Exact local database choice and schema strategy
- Exact URL canonicalization policy
- Whether BFS is enforced strictly level-by-level or through an equivalent validated mechanism
- Exact search indexing method and ranking formula
- CLI-only MVP versus CLI plus local web UI
- Resume behavior for in-progress frontier items after interruption

## 16. Definition of MVP

The MVP is complete when all of the following are true:

- A local user can start a crawl from an origin URL with depth `k`.
- The crawler performs BFS up to depth `k` without recrawling the same canonical URL in the same job.
- Search can run during indexing and returns `(relevant_url, origin_url, depth)`.
- The system persists to a local database and survives ordinary page-level failures.
- The user can inspect system state through a simple CLI or UI.
- Back pressure is present and observable.
- The core behavior is documented clearly enough for other agents and evaluators to understand and verify it.
