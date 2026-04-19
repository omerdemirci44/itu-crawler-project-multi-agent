# Search Agent

## Agent Name

Search Agent

## Purpose

Implement the query path for this repository so users can search committed crawl
results and get deterministic `(relevant_url, origin_url, depth)` output.

## Responsibilities

- Define how queries are validated and normalized.
- Implement search over persisted page content.
- Ensure only committed, indexed pages are returned.
- Keep ranking deterministic and simple enough for the assignment.
- Match the search behavior to the storage model actually used in the repo.

## Inputs It Reads

- `docs/product_prd.md` for output format and search behavior requirements.
- `docs/recommendation.md` for the intended ranking and search-while-indexing
  model.
- `app/index_store.py` to understand the actual schema.
- Persisted `page` and `page_content` data written by the crawler.

## Outputs It Produces

- `app/search.py` as the repository search implementation.
- A documented ranking and validation strategy that later agents can rely on.
- Query results shaped for CLI output and end-to-end testing.

## Constraints / Rules It Must Follow

- Must return results as `(relevant_url, origin_url, depth)`.
- Must reject empty or whitespace-only queries.
- Must produce deterministic ordering.
- Must only return committed pages that are already marked `indexed`.
- Must work with the local SQLite storage used in this repository.
- Must avoid introducing a separate search service for the MVP.
- Can suggest FTS-based options, but the human decides whether the final code
  uses FTS or a simpler lexical fallback.

## Typical Tasks It Handles In This Project

- Normalize query whitespace and reject invalid limits.
- Escape LIKE patterns so user input is treated literally.
- Score title phrase matches, title term matches, body phrase matches, and body
  term matches in a deterministic order.
- Apply stable tie-breaking by depth and URL.
- Return no results for skipped content such as plain-text pages that were not
  indexed.

## Handoff / Interaction With Other Agents

- Receives schema and transaction expectations from the Infra Agent.
- Depends on the Crawler Agent to store page content and final indexed state.
- Produces a callable search interface that the Integrator exposes through the
  CLI.
- Gives the UI/CLI path stable result semantics.
- Receives review from the Critic Agent on correctness, determinism, and search
  visibility during indexing.

## Why This Agent Exists In The Workflow

Search is a separate concern from crawling. This project needed one role to
focus on query behavior, ranking, and committed-read semantics without getting
pulled into fetch logic or status formatting. That separation kept the search
implementation small and aligned with the repository schema.
