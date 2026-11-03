# Manifest Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `prumi serve` notices a new dbt semantic manifest and swaps it in after the startup checks pass, keeping the old one (and logging why) when they fail.

**Architecture:** `Services` holds one immutable `Snapshot(source, engine)`; every request takes it once, and `Services.swap()` replaces it whole. A `SourceWatcher` in core polls the files the adapter names through an optional `watched_files()` method, rebuilds the source with `Source.from_config` (which runs every startup check) and swaps it in. `serve()` runs the watcher in a daemon thread.

**Tech Stack:** Python 3.12, Starlette/uvicorn, FastMCP, pytest; dbt adapter with MetricFlow 0.213.0.

**Spec:** `docs/superpowers/specs/2026-11-03-manifest-reload-design.md`

## Global Constraints

- Core never imports an adapter; it learns the files to watch only through the adapter's optional `watched_files() -> Sequence[Path]`. An adapter without it (or returning nothing) is never reloaded.
- `watched_files` is NOT added to the `@runtime_checkable` `Adapter` Protocol's members (that would make `isinstance` fail for adapters without it); core reads it with `getattr(adapter, "watched_files", None)`. The protocol module documents it.
- Poll every **2 seconds**; load only when a file's `(st_mtime_ns, st_size)` (or "missing") is the same on **two polls in a row** after a change; retry a failed reload every **30 seconds** while the files stay the same, and at once (after the stable-twice rule) when they change.
- One WARNING per version of the files; none on retries; none while nothing changes. Success: one INFO line.
- An `AdapterStartupError` is logged with its message; any other exception by `type(exc).__name__` only (driver messages can hold connection details).
- Startup is unchanged: a bad manifest still stops `prumi serve` before it binds.
- Only `prumi serve` starts the watcher; other CLI commands and `Services` built in tests don't poll.
- Code never cites designs or plans; tests are named after the behavior they check; tests never read `docs/superpowers/`.
- Docs change in the same commit as the code they describe. Never edit `CHANGELOG.md`, versions or tags.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UBRiAV4WPtvWK2bPQHs39y
  ```

## Review Focus

1. **A home page request racing a swap** stores the old source's "data through" after the swap emptied the cache, and serves it for 10 minutes. Expected: a date computed on an old snapshot is never cached as the current one (Task 1, `test_data_through_computed_on_an_old_snapshot_is_not_kept`).
2. **`dbt build` writes the manifest before it creates a new model's table**: the first reload fails, and nothing ever retries if only file changes trigger loads. Expected: a retry 30 s later succeeds without the file changing, with no second warning (Task 3, `test_a_failed_reload_is_retried_every_30_seconds_without_a_new_warning`; Task 4 on a real DuckDB copy).
3. **A failing reload whose exception text carries a secret** (a driver error that isn't an `AdapterStartupError`). Expected: only the type name is logged (Task 3, `test_a_failure_that_isnt_a_startup_error_is_logged_by_type_only`).
4. **The watcher thread dying on an unexpected error** (e.g. `stat` raising `PermissionError`) stops all later reloads silently. Expected: a `stat` error counts as "missing" for that file, and the loop survives any exception from one poll (Task 3, `test_a_stat_error_counts_as_missing_and_the_watcher_keeps_going`).
5. **Postgres on reload**: a manifest naming another database must be refused with the same message as at startup and the old manifest kept (Task 4, `test_a_manifest_for_another_postgres_database_is_refused_on_reload`).

---

### Task 1: One snapshot per request, swapped whole

**Files:**
- Modify: `packages/prumi-core/src/prumi/services.py`
- Modify: `packages/prumi-core/src/prumi/server/app.py` (`index`, `shell`, `block`, `filter_values`)
- Modify: `packages/prumi-core/src/prumi/mcp/__init__.py` (every tool and helper that reads `services.source` / `services.engine`, lines ~261, 282, 361, 712–739)
- Modify: `packages/prumi-core/tests/test_mcp.py:396-404` (replace the manual `demo_services.source = …` + `__dict__.pop("engine")` with `demo_services.swap(later.source)`)
- Test: `packages/prumi-core/tests/test_services.py` (new)

**Interfaces:**
- Produces, in `prumi/services.py`:
  - `@dataclass(frozen=True) class Snapshot: source: Source; engine: Engine`
  - `Services.current() -> Snapshot`: the snapshot in use; loads the source on first call (under a lock; `AdapterStartupError` as today).
  - `Services.swap(source: Source) -> None`: replaces the snapshot with `Snapshot(source, Engine(source, as_of))` in one assignment and empties the "data through" cache.
  - `Services.source` / `Services.engine`: read-only properties returning `current().source` / `current().engine`, kept for one-off callers (CLI, tests); `Services.source` also gets a setter equal to `swap(value)` so existing fixtures (`services.source = demo_source`) keep working.
  - `Services.check_page(..., source: Source | None = None)`: uses `source` for labels and formats when given, else `current().source`.
- Request handlers and MCP tools call `services.current()` once at their start and pass the snapshot (or its parts) down; none reads `services.source` and `services.engine` separately. `Services.validate` takes one snapshot and passes `source=snapshot.source` into the `check_page` partial.
- The freshness cache stores the snapshot it was computed on: `_freshness: tuple[Snapshot, float, date | None] | None`. A cached value whose snapshot isn't the current one is stale; `data_through()` computes on the snapshot it read and stores the result only if that snapshot is still current.

- [ ] **Step 1: Write the failing tests** in `test_services.py` (use `demo_services` and `fake_source` from `packages/prumi-core/tests/conftest.py`; `other_source` below is `fake_source`, a `Source` over the suite's `FakeAdapter`, whose dates come from the module-level `DATA_THROUGH` dict that tests may monkeypatch, as `test_home.py::test_freshness_is_cached_for_ten_minutes` does):

```python
def test_swap_replaces_source_and_engine_together(demo_services, fake_source):
    other_source = fake_source
    before = demo_services.current()
    demo_services.swap(other_source)
    after = demo_services.current()
    assert after.source is other_source and after.engine.source is other_source
    assert before.engine.source is before.source  # the old snapshot stays whole

def test_current_is_the_same_snapshot_until_a_swap(demo_services):
    assert demo_services.current() is demo_services.current()

def test_a_swap_empties_the_data_through_cache(demo_services, fake_source, monkeypatch):
    # fake_source answers data_through with a date; swapping to a source with a later date
    # shows the later date on the next call, inside the 10-minute TTL.
    ...
    assert demo_services.data_through() == later_date

def test_data_through_computed_on_an_old_snapshot_is_not_kept(demo_services, ...):
    # Patch the engine's data_through so that, mid-computation, it calls
    # demo_services.swap(newer_source). The returned date may be the old one, but the next
    # data_through() call computes again on the new snapshot.
    ...

def test_validation_uses_one_snapshot_even_if_a_swap_happens_meanwhile(demo_services, fake_source, monkeypatch):
    other_source = fake_source
    seen = {}
    def fake_validate(spec, html, source, ids, *, engine, page_check, **kw):
        seen["source"], seen["engine_source"] = source, engine.source
        demo_services.swap(other_source)
        seen["page_check_source"] = page_check.keywords["source"]
        return Report(())
    monkeypatch.setattr("prumi.services.validate", fake_validate)
    original = demo_services.current().source
    demo_services.validate({}, "")
    assert seen == {"source": original, "engine_source": original, "page_check_source": original}
```

Also, in `test_mcp.py`: `test_search_metrics_sees_a_swapped_catalog` — after `demo_services.swap(source_without_discount_rate)`, the `search_metrics` tool no longer returns `discount_rate`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/prumi-core/tests/test_services.py packages/prumi-core/tests/test_mcp.py -k "swap or snapshot or data_through" -v`
Expected: FAIL (`Services` has no `current`/`swap`).

- [ ] **Step 3: Implement** `Snapshot`, `current()`, `swap()`, the properties and setter, the snapshot-tagged freshness cache, and the call-site changes in `app.py`, `mcp/__init__.py` and `Services.validate`/`check_page`/`_latest_data`. `serve()` keeps calling `services.source` to load at startup (or `services.current()`).

- [ ] **Step 4: Run the whole core suite**

Run: `uv run pytest packages/prumi-core -q`
Expected: all pass (existing fixtures that assign `services.source` still work through the setter).

- [ ] **Step 5: Commit** — `Give each request one snapshot of the source, swappable whole`

---

### Task 2: The dbt adapter names the file to watch

**Files:**
- Modify: `packages/prumi-core/src/prumi/adapters.py` (document the optional method next to the `Adapter` protocol; not a Protocol member)
- Modify: `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/adapter.py` (`DbtAdapter.__init__` takes `manifest_path: Path`; `from_config` passes the resolved path)
- Modify: `packages/prumi-core/README.md` (the adapter protocol: one sentence on the optional `watched_files()`)
- Test: `packages/prumi-adapter-dbt/tests/test_startup.py` (it already builds adapters from config) and `packages/prumi-core/tests/test_adapters.py`

**Interfaces:**
- Produces: `DbtAdapter.watched_files() -> tuple[Path, ...]` returning `(the resolved semantic_manifest path,)`.
- Produces, in `prumi/adapters.py`: `watched_files_of(adapter: Adapter) -> tuple[Path, ...]` — `tuple(adapter.watched_files())` when the adapter has the method, else `()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_adapter_watches_its_resolved_manifest(demo_project, tmp_path):
    # as test_relative_paths_resolve_against_the_config_folder builds its adapter, from a
    # config whose semantic_manifest is relative to config_dir
    assert adapter.watched_files() == ((config_dir / relative).resolve(),)
```
In core (`packages/prumi-core/tests/test_adapters.py`):
```python
def test_an_adapter_without_watched_files_watches_nothing():
    assert watched_files_of(object_with_protocol_methods_only) == ()
    assert isinstance(object_with_protocol_methods_only, Adapter)  # still an Adapter
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest packages/prumi-adapter-dbt/tests/test_startup.py packages/prumi-core/tests/test_adapters.py -k watch -v`; expected FAIL.
- [ ] **Step 3: Implement** both, and the README sentence.
- [ ] **Step 4: Run** `uv run pytest packages/prumi-adapter-dbt -q` and the core test; expected PASS.
- [ ] **Step 5: Commit** — `Let adapters name the files whose change means reloading them`

---

### Task 3: The watcher's rules

**Files:**
- Create: `packages/prumi-core/src/prumi/reload.py`
- Test: `packages/prumi-core/tests/test_reload.py`

**Interfaces:**
- Consumes: `watched_files_of` (Task 2); `Services.current()`, `Services.swap()` (Task 1); `AdapterStartupError`.
- Produces, in `prumi/reload.py`:
  - `POLL_S = 2.0`, `RETRY_S = 30.0`
  - `class SourceWatcher:`
    - `__init__(self, files: Sequence[Path], load: Callable[[], Source], swap: Callable[[Source], None], clock: Callable[[], float] = time.monotonic)`; reads the files' signatures at construction as the baseline (the startup state).
    - `check(self) -> None`: one poll. Never raises.
    - `start(self, interval: float = POLL_S) -> None` / `stop(self) -> None`: a daemon thread calling `check()` every `interval` until stopped (`threading.Event.wait`), joined on `stop`.
  - `watcher_for(services: Services) -> SourceWatcher | None`: `None` when the current adapter watches no files; else a watcher whose `load` is `lambda: Source.from_config(services.config.sources[DEFAULT_SOURCE])` and whose `swap` is `services.swap`.
- Logger: `logging.getLogger("prumi.server")`. Messages (exact):
  - INFO: `reloaded the source after {names} changed: {n} metrics`
  - WARNING: `{names} changed, but the source wasn't reloaded; keeping the current one: {reason}` — `reason` is `str(exc)` for `AdapterStartupError`, else `type(exc).__name__`.
  - `{names}` is the changed files' names joined by `, ` (file name only, not the full path).

The state machine (it isn't determined by the signatures, so here it is):

```
signature(file) = (st_mtime_ns, st_size), or None if stat raises OSError
state: seen (last signatures), pending (bool), failed (signatures of the last failed load or None), retry_at

check():
  now_sigs = signatures
  if now_sigs != seen: seen = now_sigs; pending = True; return        # changed: wait one poll
  if pending: pending = False; attempt(warn = now_sigs != failed); return  # stable twice
  if failed == now_sigs and clock() >= retry_at: attempt(warn = False)
attempt(warn):
  try: source = load(); swap(source); failed = None; log INFO
  except Exception as exc: failed = seen; retry_at = clock() + RETRY_S; if warn: log WARNING
```
`check()` wraps its whole body so no exception escapes.

- [ ] **Step 1: Write the failing tests** (fake clock, real temp files, `load`/`swap` as fakes recording calls, `caplog` at DEBUG):

```python
def test_nothing_happens_while_the_files_dont_change(...): # 5 checks → load never called, no records
def test_a_change_is_loaded_once_it_is_the_same_on_two_polls(...):
    # write file → check() → load not called; check() → load called once, swap got its source
def test_a_file_still_being_written_is_not_loaded(...):
    # write, check, append, check → load not called; check → called once
def test_a_successful_reload_logs_one_info_line(...):
    # message == "reloaded the source after semantic_manifest.json changed: 3 metrics"
def test_a_failed_reload_keeps_the_current_source_and_warns_once(...):
    # load raises AdapterStartupError("The semantic manifest isn't valid JSON."); swap never called;
    # exactly one WARNING containing that message; more checks without changes → no more records
def test_a_failed_reload_is_retried_every_30_seconds_without_a_new_warning(...):
    # fail once; clock +29 → check → no load; clock +31 → check → load called again (now succeeds)
    # → one INFO, still one WARNING total
def test_a_new_version_after_a_failure_warns_again(...):
    # fail, change file, two checks, fail again → two WARNINGs
def test_a_missing_file_is_a_failed_reload(...):
    # delete file, two checks → load called (it raises the startup "missing" error) → one WARNING
def test_a_failure_that_isnt_a_startup_error_is_logged_by_type_only(...):
    # load raises RuntimeError("password=sentinel-secret") → WARNING ends with ": RuntimeError";
    # "sentinel-secret" in no record
def test_a_stat_error_counts_as_missing_and_the_watcher_keeps_going(...):
    # monkeypatch Path.stat to raise PermissionError once; check() doesn't raise
def test_the_thread_stops_when_asked(...):
    # start(interval=0.01); stop() returns within 1 s; the thread is no longer alive
def test_no_watcher_for_an_adapter_that_watches_nothing(...):
    # services whose adapter has no watched_files → watcher_for(services) is None
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest packages/prumi-core/tests/test_reload.py -v`; expected FAIL (no module).
- [ ] **Step 3: Implement** `prumi/reload.py`.
- [ ] **Step 4: Run** `uv run pytest packages/prumi-core/tests/test_reload.py -v`; expected PASS.
- [ ] **Step 5: Commit** — `Watch the source's files and swap in a new source when they pass the checks`

---

### Task 4: `prumi serve` reloads, end to end, and the docs

**Files:**
- Modify: `packages/prumi-core/src/prumi/server/__init__.py` (`serve()` starts `watcher_for(services)` after the startup checks and before `uvicorn.run`; stops it in `finally`)
- Test: `packages/prumi-core/tests/test_reload_demo.py` (real demo; DuckDB) and a Postgres case in `packages/prumi-core/tests/test_postgres_demo.py`
- Modify: `README.md`, `docs/configuration.md`, `packages/prumi-adapter-dbt/README.md`

**Interfaces:**
- Consumes: `watcher_for`, `SourceWatcher.check()` (Task 3); `Services.current()` (Task 1); `write_demo_config` / `make_demo_config(directory, manifest)`, `write_postgres_demo_config`, `postgres_demo` from the conftests.

- [ ] **Step 1: Write the failing tests.** Each copies the demo manifest into `tmp_path`, points a fresh `Services` at the copy (`make_demo_config(tmp_path, copy)`), builds `watcher = watcher_for(services)`, edits the copy, and calls `watcher.check()` twice (bumping the file's mtime with `os.utime` so the change is seen even within the filesystem's timestamp resolution).

```python
def test_a_changed_metric_label_reaches_the_mcp_catalog_without_a_restart(...):
    # change revenue's label to "Revenue (net)"; after two checks, the MCP describe_metric
    # (or search_metrics) result for "revenue" carries "Revenue (net)"

def test_a_manifest_that_doesnt_parse_keeps_the_old_catalog(...):
    # write "{" → two checks → services.current() is the same snapshot; one WARNING whose text
    # equals the AdapterStartupError message startup gives for that file (compare with
    # pytest.raises(AdapterStartupError) on Services(...).source for the same file)

def test_a_manifest_naming_a_missing_table_loads_once_the_table_exists(...):
    # copy demo.duckdb into tmp_path and point the config at the copy; change one semantic
    # model's relation to a new table name → two checks → kept, one WARNING naming the table;
    # create the table in the copy (duckdb.connect(copy) CREATE TABLE ... AS SELECT * FROM the
    # original) → advance the watcher's clock past RETRY_S → check → the new snapshot is
    # current; still one WARNING

def test_serve_starts_and_stops_the_watcher(monkeypatch, ...):
    # monkeypatch uvicorn.run to record that a watcher thread is alive during it (and the
    # checker as test_server.py does); after serve() returns, the thread is stopped
```
Postgres (`@pytest.mark.postgres` via the `postgres_demo` fixture):
```python
def test_a_manifest_for_another_postgres_database_is_refused_on_reload(...):
    # copy the Postgres manifest, rewrite every relation's database to "other_db" → two checks
    # → kept; one WARNING with the same text startup gives for that manifest
```

- [ ] **Step 2: Run to verify they fail** — `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest packages/prumi-core/tests/test_reload_demo.py packages/prumi-core/tests/test_postgres_demo.py -k reload -v`; `test_serve_starts_and_stops_the_watcher` fails, the others may already pass on Tasks 1–3 (fine: they pin the end-to-end behavior).
- [ ] **Step 3: Implement** the `serve()` change.
- [ ] **Step 4: Update the docs**
  - `README.md`: delete "Manifest changes need a restart." from Known limitations; where `prumi serve` is described, add that the server picks up a new manifest a few seconds after `dbt parse` writes it, and that `prumi.toml` changes still need a restart; in the troubleshooting list, that a manifest failing a check while the server runs is logged and the old one kept, and retried every 30 seconds.
  - `docs/configuration.md`: in the `semantic_manifest` row, "The server reloads it when it changes."
  - `packages/prumi-adapter-dbt/README.md` ("What Prumi reads"): the manifest is watched while the server runs.
- [ ] **Step 5: Run everything**

Run: `PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest -q && uv run ruff check && uv run ruff format --check`
Expected: all pass, no skips for Postgres.

- [ ] **Step 6: Commit** — `Reload the manifest while prumi serve runs`
