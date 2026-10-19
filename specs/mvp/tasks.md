# MVP tasks

Ordered. Each task is one commit (or a short series) and ends with its tests passing. AC numbers refer to [spec.md](spec.md#acceptance-criteria). If a task hits something the spec doesn't answer, write it in `questions.md` and stop.

## Demo project and adapter

- [x] 1. Demo data generator (`examples/demo-dbt-project/generate.py`, seeded, writes the `raw` schema of `demo.duckdb`) — done when two runs produce identical tables and the row counts, date range and NULL regions match spec section 15 (AC 1)
- [x] 2. Demo dbt project: models, semantic models, the nine metrics with `config.meta.prumi` formats, `build.py`, demo `prumi.toml`, README listing the traps — done when `build.py` writes `demo.duckdb` and `target/semantic_manifest.json` from a clean folder (AC 1, 5)
- [x] 3. pytest session fixture that builds the demo when outputs are missing or stale; `demo` marker — done when `uv run pytest` on a clean clone builds it once and reuses it (AC 1)
- [x] 4. Adapter protocol types in `prumi.adapters` (`Adapter`, `Capabilities`, `Catalog`, `Metric`, `Dimension`, `SemanticQuery`, `Filter`, `OrderBy`, `Viewer`, `LOCAL_VIEWER`, `Result`) and entry-point discovery — done when a fake adapter registered in a test is found by name, and a test fails if `prumi` imports an adapter package (AC 2)
- [x] 5. `_metricflow.py`: manifest loading, compile-only client per dialect, `compile(query) -> sql`, and the import-boundary test — done when the demo manifest loads and a simple query compiles with zero client calls (AC 2, 3)
- [x] 6. **Contract test, first:** port the spike's checks onto the demo project and add the behaviors section 4 relies on that the spike didn't test (window without a time group-by, dimension values, `order_by`/`limit`, `not_in` + NULL, `non_additive_dimension`, Monday weeks). **If any of these doesn't work as the spec assumes, record it in `questions.md` and stop** — done when the contract test passes (AC 3)
- [x] 7. DuckDB connection (`connections/duckdb.py`), `execute` with renaming to semantic column names and value normalization, startup checks — done when each startup failure case has a clear message (AC 4)
- [x] 8. Filter rendering: quoting, `{`/`}` rejection, number/bool literals, NULL semantics — done when the injection and NULL tests pass against the demo (AC 6)
- [x] 9. Catalog extraction: kinds, additivity rule, `meta.prumi` format, owner, dimensions, `relations` — done when the demo catalog matches the spec table (AC 5)

## Engine and validation

- [x] 10. `prumi.time`: grains, `as_of`, window forms, snapping, grain compatibility, comparison windows — done when the table-driven tests pass (AC 7, 8)
- [x] 11. Config loading (`prumi.toml`, relative paths, loopback-only check) — done when a non-loopback host fails to load with a clear message (AC 20)
- [x] 12. Spec models (format v0), YAML/JSON loading, committed JSON Schema and its up-to-date test (AC 16)
- [x] 13. Validator static rules: `spec.*`, R2, R3, R4, R5, R6, R7, R10, with the issue format and one fixture per code — done when each fixture produces exactly its code (AC 12, 13, 14)
- [x] 14. HTML checks: R8 heuristic with the `fixtures/r8/` set, bindings, `prumi.on` references, allowed hosts, unpinned scripts, size limit (AC 12, 15)
- [x] 15. Engine block execution: query building from block + filter values, row cap, top N, Others (additive and `not_in`), totals, comparisons and deltas, freshness and `R3.stale_data`, `_partial`, block result JSON — done when the R1 and R6 tests match hand-written SQL (AC 9, 10, 11)
- [x] 16. Validator dry run (`query.failed`) and `for_agent` with the 200-row cap (AC 12, 28)

## Registry, CLI, server

- [x] 17. Registry: SQLite schema and migrations, WAL, publish with versions, canonical spec and fingerprint (golden test), computed status, delete (AC 17, 18)
- [x] 18. CLI: `serve`, `list`, `certify` (with re-validation), `uncertify`, `delete`, `requests`, `validate`, exit codes (AC 18, 19)
- [x] 19. HTTP app: Host check, routes, block/status/values APIs that accept only filter values, error codes (AC 20, 21)
- [x] 20. Frame serving: runtime injection, CSP and sandbox headers; shell page (bar, badge, freshness, warnings toggle) (AC 22)
- [x] 21. Runtime and shell scripts: postMessage protocol v0, `prumi.*` API, `click` interactions, formatting, the outside-Prumi notice, served from the workspace and the wheel (AC 23, 24, 31)
- [x] 22. Browser test harness (Playwright, headless Chromium, `browser` marker) and the hosting/runtime browser tests (AC 22, 23, 24)

## MCP and end to end

- [x] 23. MCP server: FastMCP mounted at `/mcp` (stateless Streamable HTTP), the six tools, `find_metrics` ranking, instructions text — done when an MCP client test lists the tools and calls each one (AC 26, 27, 28)
- [x] 24. Query logging without values, plus the sentinel test (AC 25)
- [x] 25. End-to-end test: scripted MCP session → browser shows numbers equal to SQL → certify → edit → draft (AC 29)
- [x] 26. README: quick start, "Connect your agent", "Where your data goes" (AC 30)
- [x] 27. Release prep: versions to 0.1.0, `uv build` wheels checked for `runtime.js`/`shell.js`, changelog entry. The maintainer tags and publishes (AC 31)

## First-use fixes (spec section 18; added 2026-10-18)

- [x] 28. Plain `spec.invalid` messages: map pydantic error types to the section 18 wording, with fixtures for the five kinds (AC 34)
- [x] 29. `prumi.format` accepts a format object, and warns once in the console for unknown arguments (AC 35)
- [x] 30. Candidates: `candidate_id` in `validate_spec`, the in-memory store, `publish_dashboard(candidate_id)` with re-validation and `publish.unknown_candidate` (AC 33)
- [x] 31. `authoring_guide` tool and its Markdown file, with the three tests (AC 32)
- [x] 32. Local-only note on publish; new instructions text; README updated for the new tool (AC 36)
- [x] 33. Rerun the README's example prompt in a fresh agent session and write down what happened (AC 37). Done 2026-10-18: no spec guessing, HTML sent once, published by `candidate_id`; 373 s, ~42k output tokens.

## Trusted numbers, editing and the second-run fixes (spec section 19; added 2026-10-18)

- [x] 34. **Provenance proof, first:** the number matcher and the fixtures of 19.1 (copy the two earlier dashboards from `~/test` and `~/test2`, write the probe page from the second review), run in a test browser. **If the must-pass pages can't pass without letting a must-fail page through, record it in `questions.md` and stop** (AC 38)
- [x] 35. Server check: Chromium lifecycle, rendering the candidate with dry-run results, isolated-world scan, the five codes, publish refusal; Chromium as a dependency with the startup check (AC 38, 39, 40, 49)
- [x] 36. Browser check: the runtime's matching after render, reports to the shell, the check endpoint and registry table (AC 41)
- [x] 37. Certification refuses on server-check errors and on flagged browser reports (AC 41)
- [x] 38. `prumi.format(value, metric, options)` (AC 42)
- [x] 39. Visible errors in bound elements (AC 43)
- [x] 40. `html.network_call` and `html.blocked_request` (AC 44)
- [x] 41. `list_dashboards`, `get_dashboard`, `can_read`/`can_edit` and `dashboard.forbidden` (AC 45)
- [x] 42. Editing with `base` + `html_edits` (AC 46)
- [x] 43. Default theme: `/theme.css`, injection, CSP, contrast test (AC 47)
- [x] 44. Shutdown within 5 s (AC 48)
- [x] 45. Guide, instructions, `docs/bi-rules.md` and README updates (AC 50)
- [x] 46. Benchmark run (AC 51). Done 2026-10-18 (third run): 373 s to publish, of which ~38 s asking about existing dashboards; ~43k output tokens; one correct refusal (a JS error); a richer page than runs 1 and 2.
- [x] 47. Remove every spec-section and AC reference from code, comments and test names (rewrite each as the *why*, or as a pointer to an ADR, a BI rule or an issue code), and make the tests that read `specs/` compare against the shipped text instead (see AGENTS.md, code style). Done when `git grep -nE "spec(\.md)? section|\bAC [0-9]|specs/" -- packages examples` finds nothing. Do it last, after tasks 34–45

## Tightening after the proof (spec section 19.13; added 2026-10-18)

- [x] 48. Fixtures first: bindings in the first-run fixture's spec, and the four new must-fail pages (AC 52)
- [x] 49. `R8.copied_number`: the source scan at the server check (AC 53)
- [x] 50. Bindings decide what a number may match, lists of blocks per binding, `R8.unbound_number`, in both checks (AC 54)
- [x] 51. `html.iframe`, `html.closed_shadow_root`, `html.image` and `img-src 'none'` (AC 55)
- [x] 52. Unmarked ticks only inside `<svg>` (AC 56)
- [x] 53. The viewer bar and `needs_attention` for flagged published versions (AC 57)
- [x] 55. Hover pass in the server check, with the unbound-tooltip fixture (AC 58). Do it after task 50
- [x] 54. Guide and `docs/bi-rules.md`: bindings for every number, tooltips inside bound elements, no images, iframes or closed shadow roots, the new codes

## Polish from the third run (spec section 19.14; added 2026-10-18)

- [x] 56. Add the third-run dashboard as a must-pass fixture (AC 64)
- [x] 57. Candidates for failed validations, `publish.invalid_candidate`, plain dashboard id as `base`, `validate.invalid_base` (AC 59)
- [x] 58. Line, column and stack for `html.runtime_error` (AC 60)
- [x] 59. Evenly spaced ticks at any precision in SVG and axis-marked elements (AC 61)
- [x] 60. `check_states` in `validate_spec` and publish (AC 62)
- [x] 61. Guide additions (AC 63)

## Layout report from validation (spec section 19.15; added 2026-10-18)

- [x] 62. Layout measurements in the server check: the five `layout.*` warnings, devices, no page text in issues (AC 65, 66, 67)
- [x] 63. Guide additions for the layout report

## No company data reaches the agent (spec section 19.16; added 2026-10-18)

- [x] 64. `query` returns the profile; remove the 200-row cap from `for_agent` (AC 69)
- [x] 65. Scrub agent-facing messages: `R8.*`, layout issues, browser reports in `get_dashboard`, source errors (AC 70)
- [x] 66. The sentinel test across every MCP tool output (AC 68)
- [x] 67. README, guide and instructions wording (AC 71)

## Home page (spec section 20; added 2026-10-18)

- [x] 68. Home page: header with source and freshness, dashboard cards with status, empty state, metric requests, footer; `/home.css`, CSP, escaping (AC 72, 73, 74, 76)
- [x] 69. `/home.js`: search and copy button; browser tests for search, copy, phone width and both themes (AC 75)

## One display locale (spec section 21; added 2026-10-18)

- [x] 70. `[server] locale`: config and startup check; `prumi.format` and `prumi.locale` use it (AC 77, 81)
- [x] 71. Number reader in the configured locale for the server and browser checks; the server check renders in it; the fourth-run page as a fixture (AC 78, 79)
- [x] 72. Second-language render and `R8.browser_locale` (AC 80)
- [x] 73. Guide, server instructions and README: one locale per server, `prumi.format` and `prumi.locale`
- [x] 74. Dates and periods in the configured locale, from `Intl` (rule 6; AC 82)
- [x] 75. Test-only switch for the second render; the suite's default validation fixture uses it (rule 7; AC 83)
- [x] 76. Drop narrow units from the period words; `12m` is flagged again in `en-US` (rule 6; AC 82)

## Polish from the fourth and fifth runs (spec section 22; added 2026-10-18)

- [x] 77. Server instructions: build with Prumi, not the client's own dashboard tool (AC 84)
- [x] 78. `prumi.change`: runtime, number check, guide row, guide line and example (AC 85)
- [x] 79. The viewer bar on a phone (AC 86)

## No company data in the registry (spec section 23; added 2026-10-19)

- [x] 80. Docstrings in `prumi/agent_output.py` and `prumi/mcp/__init__.py`: only `query` results pass through `for_agent`; issue messages and check reports are cleaned where they're built — done when neither docstring says every agent result passes through it (wording; no AC)
- [x] 81. Server side: redacted state and state key, flagged items reduced to code, path and count, errors cleaned with the report's filter values and the number rule, `text` and `blocks` accepted but never stored, migration 3, `prumi certify` output, `get_dashboard` reads the stored report, README "What Prumi stores" (AC 87, 88, 90)
- [ ] 82. Shell side: post flagged items without `text` and `blocks`, clean error messages with the block results the shell holds; the viewer bar keeps the text (AC 89)

