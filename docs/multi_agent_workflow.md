# Multi-Agent Workflow

## Purpose

This document explains the multi-agent workflow used to develop this project for the assignment. It describes how the work was split across separate agent-oriented chats, how the outputs were consolidated, and how those outputs map to the current repository.

Important clarification: the final runtime system is **not** a multi-agent runtime. When the project runs, it is a normal Python application with modules such as `app/main.py`, `app/crawler.py`, `app/search.py`, and `app/status.py`. The multi-agent requirement was satisfied in the **development workflow**, not in the production runtime.

## Why A Multi-Agent Workflow Was Used

The project had a natural decomposition:

- requirements definition
- architecture and storage design
- crawling and parsing
- search
- operator-facing status and CLI integration
- review and validation

Using separate agents for those areas made the work more manageable. Each agent could produce focused alternatives and implementation drafts without mixing all concerns into one long conversation. That was useful for this assignment because the system has clear module boundaries and the deliverables include both documentation and working code.

The multi-agent setup was practical for development, not theoretical. It helped with:

- turning the assignment brief into a concrete PRD first
- freezing core design choices before implementation spread across modules
- letting implementation-oriented agents work against stable interfaces
- keeping review pressure on BFS correctness, deduplication, persistence, and search behavior

## Agents Defined

The development workflow used these agents:

| Agent | Main responsibility | Main repo evidence |
| --- | --- | --- |
| PRD Agent | Convert the assignment into concrete product requirements and acceptance criteria | `docs/product_prd.md` |
| Infra Agent | Choose the MVP architecture and implement the storage layer and crawl lifecycle helpers | `docs/recommendation.md`, `app/index_store.py` |
| Crawler Agent | Implement URL normalization, HTML parsing, fetch flow, and BFS crawl coordination | `app/parser.py`, `app/crawler.py` |
| Search Agent | Implement query validation, ranking, and search over committed indexed content | `app/search.py` |
| UI Agent | Implement the operator-facing status view for the CLI | `app/status.py` |
| Critic Agent | Review proposals and implementation drafts for correctness gaps, edge cases, and scope control | Reflected indirectly in accepted/rejected decisions rather than a standalone module commit |
| Integrator | Consolidate module outputs, wire the CLI, and verify the full system end to end | `app/main.py`, `tests/test_end_to_end.py` |

## Responsibility Of Each Agent

### PRD Agent

The PRD Agent produced the requirements baseline in [`docs/product_prd.md`](./product_prd.md). That document defined the actual scope used by the rest of the workflow:

- localhost-only system
- BFS crawl up to depth `k`
- no duplicate crawl of the same canonical URL within a job
- local database persistence
- searchable results as `(relevant_url, origin_url, depth)`
- operator interface for crawl, search, and status

This agent was used to reduce ambiguity before implementation started.

### Infra Agent

The Infra Agent translated the PRD into an MVP technical direction in [`docs/recommendation.md`](./recommendation.md), then into the storage layer in [`app/index_store.py`](../app/index_store.py).

Its concrete responsibilities were:

- recommend SQLite as the local database
- define schema and lifecycle states
- implement durable page/job/event storage
- implement deduplication at the database level
- add leasing and depth-tracking helpers needed by the crawler

### Crawler Agent

The Crawler Agent handled the crawl-specific logic in [`app/parser.py`](../app/parser.py) and [`app/crawler.py`](../app/crawler.py).

Its responsibilities were:

- normalize URLs consistently
- parse HTML into title, text, and outgoing links
- fetch pages with standard-library HTTP support
- enforce BFS traversal by depth
- store discovered child pages and final crawl outcomes

### Search Agent

The Search Agent implemented [`app/search.py`](../app/search.py).

Its responsibilities were:

- validate queries and limits
- search only committed indexed content
- provide deterministic ranking
- return results in the required `(relevant_url, origin_url, depth)` shape

### UI Agent

The UI Agent did not build a graphical UI. In this project, the operator-facing interface is CLI-first, so the UI work became status formatting in [`app/status.py`](../app/status.py).

Its responsibilities were:

- build a read-only status snapshot
- format crawl state for human inspection
- show counters, queue information, activity, and recent events

### Critic Agent

The Critic Agent was used as a review role, not as a feature owner. Its job was to challenge proposals and drafts, especially around:

- whether BFS was actually enforced
- whether deduplication was race-safe and durable
- whether search only saw committed data
- whether scope stayed aligned with the assignment
- whether documentation matched the implementation

There is no standalone `[Critic Agent]` feature commit in the current history. That is expected for this role. Review work affected what the human accepted, rejected, or sent back for revision.

### Integrator

The Integrator connected the module-level work into a runnable project. Its concrete outputs are [`app/main.py`](../app/main.py) and [`tests/test_end_to_end.py`](../tests/test_end_to_end.py).

Its responsibilities were:

- wire crawl, search, and status into one CLI entrypoint
- align function boundaries across modules
- validate the repo with a deterministic end-to-end test
- make sure the final repository was coherent as a single submission

## How The Agents Interacted

The workflow was organized as separate agent-oriented chats rather than one shared conversation. The interaction pattern was:

1. The PRD Agent defined the product boundary.
2. The Infra Agent used that boundary to recommend the architecture and storage model.
3. The Crawler, Search, and UI agents worked against those requirements and interfaces.
4. The Critic Agent reviewed proposals and implementation drafts and surfaced risks or mismatches.
5. The Integrator consolidated the accepted pieces into the repo and added final system wiring and validation.
6. The human reviewed outputs at each stage and decided what was actually kept.

This matters because the repository was not built by autonomous agents merging directly into each other. Work was produced in separate chats, then consolidated into a single codebase by human judgment.

## High-Level Prompts And Tasks Given To The Agents

The exact prompts are not reproduced here, but at a high level the tasks were:

- `PRD Agent`: turn the assignment into a concrete product requirements document with goals, non-goals, acceptance criteria, and delivery order
- `Infra Agent`: recommend a practical MVP architecture and then implement the local storage and crawl lifecycle helpers
- `Crawler Agent`: implement URL normalization, HTML parsing, fetching, and BFS crawl execution against the storage layer
- `Search Agent`: implement a practical search path that works on committed index data and returns the required triple output
- `UI Agent`: implement a usable operator-facing status view for the CLI-first MVP
- `Critic Agent`: review the outputs for correctness risks, scope creep, and inconsistencies between docs and code
- `Integrator`: wire the modules into one CLI and verify the integrated system with an end-to-end test

These were development tasks. They were not runtime prompts for collaborating processes inside the application.

## Decisions That Were Accepted

The following decisions were proposed by agents and accepted by the human because they fit the assignment and were practical to implement:

- **CLI-first MVP instead of a web-first system.** This appears in [`docs/recommendation.md`](./recommendation.md) and in the final CLI entrypoint [`app/main.py`](../app/main.py).
- **SQLite through the Python standard library.** This appears in the recommendation and in the implemented storage layer in [`app/index_store.py`](../app/index_store.py).
- **A modular code split by responsibility.** The final codebase keeps storage, parser, crawler, search, status, and entrypoint logic separate.
- **Database-backed deduplication using `UNIQUE(job_id, canonical_url)`.** This is the core duplicate-prevention rule in [`app/index_store.py`](../app/index_store.py).
- **Strict BFS ordering by depth.** The final crawl loop in [`app/crawler.py`](../app/crawler.py) processes unfinished depths in ascending order and only leases queued pages at the current depth.
- **Search over committed data only.** [`app/search.py`](../app/search.py) only returns rows where `page.state = 'indexed'`.
- **Read-only status reporting.** [`app/status.py`](../app/status.py) stays on the read path and does not mutate crawl state.
- **Deterministic localhost integration testing.** [`tests/test_end_to_end.py`](../tests/test_end_to_end.py) verifies crawl, deduplication, search, and status together.

## Decisions That Were Rejected Or Deferred

The multi-agent workflow also produced alternatives that were explicitly not adopted in the final runtime:

- **No multi-agent runtime in production.** The application does not run PRD/Search/Crawler/UI agents at runtime. The runtime is a normal Python CLI application.
- **No web server as the main interface for MVP.** The recommendation allowed a future UI layer, but the shipped system is CLI-first.
- **No distributed crawler, external database, or separate search engine.** Those were outside the assignment scope and unnecessary for the MVP.
- **No browser automation or JavaScript rendering.** The parser and crawler stay standard-library and HTML-focused.
- **No mandatory FTS5 dependency in the shipped version.** The recommendation preferred SQLite FTS when available, but the implemented search path in [`app/search.py`](../app/search.py) is a deterministic lexical fallback using ordinary SQLite tables.
- **No concurrent worker-pool crawler in the final implementation.** The recommendation discussed bounded worker threads and queues, but the human accepted a sequential BFS coordinator in [`app/crawler.py`](../app/crawler.py) for the first integrated version because it reduced correctness risk around ordering and state transitions.
- **No full in-flight resume logic.** The code includes a practical requeue of leased pages, but not a complex full restoration of worker execution state.

## How Final Design Decisions Were Made By The Human

The AI agents did not make the final design or implementation decisions on their own. They produced alternatives, documentation drafts, and implementation drafts. The human made the final decisions by:

- deciding which agent outputs were compatible with the assignment goals
- accepting some proposals and rejecting others
- choosing scope-reducing options when they improved correctness and delivery confidence
- consolidating separate chat outputs into one repository and linear commit history
- requiring final integration and end-to-end verification before treating the system as complete

In practical terms, the human accepted the architectural baseline from the PRD and recommendation, but also overruled or deferred some suggested complexity. The clearest example is concurrency: the recommendation describes a bounded threaded crawler, while the shipped implementation uses a simpler sequential BFS coordinator.

## How The Workflow Mapped Into The Actual Codebase And Commits

The commit history preserves the agent-oriented workflow directly in the commit subjects:

| Commit | Agent/role | What it added | Main files |
| --- | --- | --- | --- |
| `9c3f242` | Setup | Project skeleton for the assignment | `README.md`, `agents/*`, `app/*`, `docs/*` |
| `b7aa0b4` | PRD Agent | Requirements and acceptance criteria baseline | `docs/product_prd.md` |
| `08c6403` | Infra Agent | MVP technical recommendation | `docs/recommendation.md` |
| `c3d2db0` | Infra Agent | SQLite schema and storage helpers | `app/index_store.py` |
| `53ec9e0` | Crawler Agent | HTML parser and URL normalization | `app/parser.py` |
| `187ddf4` | Infra Agent | Frontier leasing and crawl lifecycle helpers | `app/index_store.py` |
| `1f611e1` | Crawler Agent | Sequential BFS crawl coordinator and fetch flow | `app/crawler.py` |
| `0f20507` | Search Agent | Search implementation | `app/search.py` |
| `7134da6` | UI Agent | Read-only crawl status snapshot and formatter | `app/status.py` |
| `b0224e7` | Integrator | CLI entrypoint wiring | `app/main.py` |
| `5b5e629` | Integrator | Deterministic end-to-end validation | `tests/test_end_to_end.py` |

This is the practical mapping from workflow to codebase:

- requirements came first
- architecture and storage followed
- crawler/search/status modules were implemented against that base
- integration happened after module-level work existed
- end-to-end validation was added last

The Critic Agent does not appear as a standalone feature commit because review work changed decisions rather than owning a single module. Its effect is visible indirectly in the conservative final choices and in the final integration and testing emphasis.

## Final Observation

This repository satisfies the assignment's multi-agent requirement through the way the project was developed:

- work was divided into separate agent-oriented chats
- agents produced alternatives and implementation drafts
- the human selected and consolidated the accepted work
- commit history reflects the contribution areas of the agents

The shipped software itself remains a conventional Python application, not a multi-agent runtime.
