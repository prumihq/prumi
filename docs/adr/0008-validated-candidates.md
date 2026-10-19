# 0008. Publishing goes through validated candidates

Status: Proposed · 2026-10-19

## Context

Agents send a dashboard's spec and HTML as tool arguments, often 20–30 KB, and every byte costs output tokens and time. In the first agent session, sending the same HTML twice (to validate it, then to publish it) took about two thirds of the session's time and output tokens. Fixing anything after validation meant sending everything again, and a later session couldn't continue work on a published dashboard.

## Decision

- **`validate_spec` returns a `candidate_id`** (the content's fingerprint) for every call with a spec and HTML, valid or not, and keeps the content in memory: at most 50 candidates, each for an hour.
- **`publish_dashboard` takes a `candidate_id`** (or a full spec and HTML) and always re-validates, since the catalog may have changed. Only a valid candidate can be published.
- **Edits instead of resending:** `validate_spec(base=..., html_edits=[{old, new}], spec=...)` edits a candidate, a published version, or a dashboard's current version. Each `old` must occur exactly once.
- **Extra filter states** (`check_states`) are stored with the candidate and checked again at publish.
- `list_dashboards` and `get_dashboard` read published dashboards back, so any session can continue work on one.

## Consequences

- The HTML is sent once per dashboard, and a fix costs the size of the edit.
- Candidates are lost on restart; the agent validates again.
- Candidates live in one server process. Several processes behind one URL would need a shared store.
