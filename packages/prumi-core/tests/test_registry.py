"""The registry: versions, fingerprints, computed status, delete, browser checks."""

import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from prumi.issues import Report, error
from prumi.registry import Content, Registry, canonical_spec_json, publish
from prumi.spec import DashboardSpec, load_spec_file

GOLDEN = Path(__file__).parent / "fixtures" / "registry"
HTML = '<div id="kpi"></div><script>prumi.on("kpi", (r) => {});</script>'


def spec(title="Sales", **block) -> DashboardSpec:
    return DashboardSpec.model_validate(
        {
            "spec_version": 0,
            "dashboard": {"id": "sales", "title": title},
            "filters": [{"id": "period", "type": "time", "default": {"last": 12, "grain": "week"}}],
            "data": [{"id": "kpi", "metrics": ["revenue"], "filters": ["period"], **block}],
        }
    )


@pytest.fixture
def registry(tmp_path):
    clock = iter(dt.datetime(2026, 10, 15, 9, 0, s, tzinfo=dt.UTC) for s in range(60))
    return Registry(tmp_path / "data", now=lambda: next(clock))


# Fingerprints


def test_golden_fingerprint():
    golden_spec = DashboardSpec.model_validate(load_spec_file(GOLDEN / "golden.yaml"))
    golden_html = (GOLDEN / "golden.html").read_bytes().decode("utf-8")
    assert "\r\n" in golden_html  # stored with \n: the fingerprint ignores line endings
    content = Content.of(golden_spec, golden_html)
    assert content.spec_hash == "5b2ce3efd46740653def2f0ddf3451e92aa4a7404043abc93019451c6d123121"
    assert content.html_hash == "929fd2153641ed2e878a894361f25cc2d938673c484734f4f4429e5eca761182"
    assert content.fingerprint == (
        "v0:b910678de015a4c6116a11c50ffd19d865521b93a74418e06510af0a583e0918"
    )
    assert content.html == golden_html.replace("\r\n", "\n")
    assert Content.of(golden_spec, content.html).fingerprint == content.fingerprint


def test_canonical_json_includes_defaults_and_sorts_keys():
    text = canonical_spec_json(spec())
    assert text.startswith('{"dashboard":{"id":"sales","title":"Sales"},"data":[{"compare":null,')
    assert '"calendar":"standard"' in text and " " not in text.replace("Sales", "")
    # Writing the defaults out, or reordering keys, doesn't change it.
    explicit = spec(dimensions=[], others=False, total=False, top_n=None)
    assert canonical_spec_json(explicit) == text


# Publishing


def test_versions(registry):
    first = registry.save(spec(), HTML)
    assert (first.version, first.created) == (1, True)
    assert registry.save(spec(), HTML) == first.__class__("sales", 1, created=False)
    same = registry.save(spec(), HTML.replace("\n", "\r\n"))
    assert (same.version, same.created) == (1, False)

    changed_spec = registry.save(spec(title="Sales, weekly"), HTML)
    assert (changed_spec.version, changed_spec.created) == (2, True)
    changed_html = registry.save(spec(title="Sales, weekly"), HTML + "<p>Notes</p>")
    assert (changed_html.version, changed_html.created) == (3, True)
    identical = registry.save(spec(title="Sales, weekly"), HTML + "<p>Notes</p>")
    assert (identical.version, identical.created) == (3, False)

    [dashboard] = registry.dashboards()
    assert (dashboard.id, dashboard.title, dashboard.owner, dashboard.current_version) == (
        "sales",
        "Sales, weekly",
        "local",
        3,
    )
    first_version = registry.version("sales", 1)
    assert first_version.spec == spec() and first_version.html == HTML
    assert registry.version("sales", 4) is None


def test_publish_validates_first(registry):
    def failing(spec, html):
        return Report.of([error("R7.unknown_metric", "data[0].metrics[0]", "No such metric.")])

    report, published = publish(registry, spec().model_dump(by_alias=True), HTML, failing)
    assert not report.ok and published is None
    assert registry.dashboards() == []

    report, published = publish(
        registry, spec().model_dump(by_alias=True), HTML, lambda s, h: Report()
    )
    assert report.ok and (published.version, published.created) == (1, True)


# Status and certification


def test_status_is_computed_from_certifications(registry):
    registry.save(spec(), HTML)
    status = registry.status("sales", 1)
    assert (status.status, status.version, status.current_version) == ("draft", 1, 1)
    assert status.certified_by is None and status.fingerprint.startswith("v0:")

    certified = registry.certify("sales", 1, by="ana")
    assert (certified.status, certified.certified_by) == ("certified", "ana")
    assert certified.certified_at == "2026-10-15T09:00:01+00:00"

    # Any change makes the current version a draft; v1 stays what was reviewed.
    registry.save(spec(title="Changed"), HTML)
    assert registry.status("sales", 2).status == "draft"
    assert registry.status("sales", 2).current_version == 2
    assert registry.status("sales", 1).status == "certified"

    # Republishing what was certified is certified again.
    back = registry.save(spec(), HTML)
    assert (back.version, back.created) == (3, True)
    assert registry.status("sales", 3).status == "certified"
    assert registry.status("sales", 3).certified_by == "ana"

    assert registry.uncertify("sales", reason="numbers changed") == 1
    assert registry.status("sales", 3).status == "draft"
    assert registry.status("sales", 1).status == "draft"
    assert registry.uncertify("sales") == 0


def test_certifying_an_unknown_version(registry):
    with pytest.raises(KeyError):
        registry.certify("sales", 1, by="ana")


def test_delete_keeps_metric_requests(registry):
    registry.save(spec(), HTML)
    registry.certify("sales", 1, by="ana")
    request = registry.request_metric("Net revenue after returns", "Finance asked", "sales")
    other = registry.request_metric("Churn", "Retention review")
    assert (request.id, request.dashboard_id, request.requested_by) == (1, "sales", "local")

    assert registry.delete("sales") is True
    assert registry.dashboards() == [] and registry.version("sales", 1) is None
    assert registry.status("sales", 1) is None
    with sqlite3.connect(registry.path) as conn:
        assert conn.execute("SELECT count(*) FROM certifications").fetchone() == (0,)
    assert [(r.id, r.dashboard_id) for r in registry.metric_requests()] == [(1, None), (2, None)]
    assert registry.metric_request_ids() == {request.id, other.id}
    assert registry.delete("sales") is False

    # The id can be used again, starting from version 1.
    assert registry.save(spec(), HTML).version == 1


# Storage


def test_schema_version_wal_and_reopening(registry, tmp_path):
    registry.save(spec(), HTML)
    with sqlite3.connect(registry.path) as conn:
        versions = conn.execute("SELECT version FROM schema_version").fetchall()
        assert versions == [(1,), (2,), (3,)]
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    again = Registry(tmp_path / "data")  # migrations don't run twice
    assert again.dashboard("sales").current_version == 1
    with sqlite3.connect(registry.path) as conn:
        assert conn.execute("SELECT count(*) FROM schema_version").fetchone() == (3,)


def test_opening_an_older_registry_drops_the_reports_it_stored_with_data(tmp_path):
    """Before migration 3, check reports kept the numbers flagged, the filters' values and the
    browser's error messages as they came."""
    from importlib.resources import files

    path = tmp_path / "data" / "registry.sqlite3"
    path.parent.mkdir()
    content = Content.of(regional(), HTML)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        for number, name in ((1, "0001_initial.sql"), (2, "0002_page_checks.sql")):
            conn.executescript(files("prumi.registry").joinpath("migrations", name).read_text())
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (number,))
        conn.execute("INSERT INTO dashboards VALUES ('sales', 'Sales', 'local', '2026-10-14', 1)")
        conn.execute(
            "INSERT INTO versions VALUES ('sales', 1, ?, ?, ?, ?, ?, '2026-10-14')",
            (content.spec_json, content.html, content.spec_hash, content.html_hash,
             content.fingerprint),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO page_checks VALUES ('sales', 1, ?, ?, 0, ?, '2026-10-14T10:00:00+00:00')",
            ('{"region":["North"]}', json.dumps(FLAGGED), '["North broke"]'),
        )

    registry = Registry(tmp_path / "data", now=lambda: dt.datetime(2026, 10, 15, 9, tzinfo=dt.UTC))
    assert registry.checks("sales", 1) == []
    assert registry.version("sales", 1).fingerprint == content.fingerprint
    registry.record_check("sales", 1, report(["North"], FLAGGED, errors=["North broke"]))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone() == (3,)
        conn.row_factory = sqlite3.Row
        [row] = conn.execute("SELECT * FROM page_checks").fetchall()
    assert dict(row) == {
        "dashboard_id": "sales",
        "version": 1,
        "state_key": state_key({"period": WINDOW, "region": ["North"]}),
        "filters_json": '{"period":{"grain":"week","last":12},"region":{"selected":1}}',
        "flagged_json": '[{"code": "R8.unverified_number", "path": "p#extra", "count": 1}]',
        "canvas": 0,
        "errors_json": '["\\u2026 broke"]',
        "reported_at": "2026-10-15T09:00:00+00:00",
    }


def test_two_registries_on_one_file_see_each_other(registry, tmp_path):
    cli = Registry(tmp_path / "data")
    registry.save(spec(), HTML)
    cli.certify("sales", 1, by="admin")
    assert registry.status("sales", 1).status == "certified"


# In-browser checks

WINDOW = {"last": 12, "grain": "week"}
FLAGGED = [{"text": "$1,234.56", "path": "p#extra", "code": "R8.unverified_number"}]


def regional() -> DashboardSpec:
    """The sales spec with a region filter too."""
    data = spec().model_dump(mode="json", by_alias=True)
    data["filters"].append(
        {"id": "region", "type": "dimension", "dimension": "customer__region", "default": []}
    )
    return DashboardSpec.model_validate(data)


def report(region, flagged=(), errors=(), canvas=False):
    from prumi.registry import CheckReport

    return CheckReport(
        filters={"period": WINDOW, "region": region},
        flagged=list(flagged),
        canvas=canvas,
        errors=list(errors),
    )


def state_key(filters) -> str:
    canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def test_the_latest_check_per_filter_state_is_kept(registry):
    registry.save(regional(), HTML)
    registry.record_check("sales", 1, report([]))
    registry.record_check("sales", 1, report(["North"], FLAGGED))
    registry.record_check("sales", 1, report(["North"], errors=["boom"]))
    checks = registry.checks("sales", 1)
    assert [(c.filters["region"], c.flagged, c.errors) for c in checks] == [
        ({"selected": 0}, [], []),
        ({"selected": 1}, [], ["boom"]),
    ]
    assert checks[0].clean and not checks[1].clean
    assert checks[0].reported_at.startswith("2026-10-15T09:00:")
    assert registry.checks("sales", 2) == []
    registry.delete("sales")
    assert registry.checks("sales", 1) == []


def test_a_stored_report_keeps_no_numbers_or_names_from_the_data(registry):
    registry.save(regional(), HTML)
    stored = registry.record_check(
        "sales",
        1,
        report(
            ["North America", "North", "N"],
            [
                {**FLAGGED[0], "blocks": ["kpi"]},
                {"text": "$7.5", "path": "p#extra"},
                {"text": "12%", "path": "p#extra", "code": "R8.unbound_number"},
                {"path": "li#note"},  # as the shell sends it now: no text, no blocks
            ],
            errors=["North America, North and N: 1,234.56 of 12 (at 2742409)"],
            canvas=True,
        ),
    )
    assert registry.checks("sales", 1) == [stored]
    # Kept apart by the full filter state, which isn't stored.
    full = {"period": WINDOW, "region": ["North America", "North", "N"]}
    assert stored.state_key == state_key(full)
    assert stored.filters == {"period": WINDOW, "region": {"selected": 3}}
    assert stored.flagged == [
        {"code": "R8.unverified_number", "path": "p#extra", "count": 2},
        {"code": "R8.unbound_number", "path": "p#extra", "count": 1},
        {"code": "R8.unverified_number", "path": "li#note", "count": 1},
    ]
    # The report's own values, longest first (one-letter values would mangle the message),
    # then any number with 3+ digits.
    assert stored.errors == ["…, … and N: … of 12 (at …)"]
    assert stored.canvas
    for found in ("North", "1,234", "7.5", "12%", "2742409"):
        assert found.encode() not in registry.path.read_bytes()


def test_reports_for_each_selection_of_a_filter_are_kept_apart(registry):
    registry.save(regional(), HTML)
    registry.record_check("sales", 1, report([], errors=["boom"]))
    assert not registry.status("sales", 1).needs_attention  # errors alone don't count
    registry.record_check("sales", 1, report(["North"], FLAGGED))
    assert registry.status("sales", 1).needs_attention
    # Both selections are stored as {"selected": 1}; a clean report for one of them leaves
    # the other's flag.
    registry.record_check("sales", 1, report(["South"]))
    assert registry.status("sales", 1).needs_attention
    assert len(registry.checks("sales", 1)) == 3
    # A clean report for the same selection clears it.
    registry.record_check("sales", 1, report(["North"]))
    assert not registry.status("sales", 1).needs_attention
    # So does a new version: only the current one can need attention.
    registry.record_check("sales", 1, report(["North"], FLAGGED))
    registry.save(regional(), HTML + "<!-- 2 -->")
    assert not registry.status("sales", 1).needs_attention
    assert not registry.status("sales", 2).needs_attention


@pytest.mark.parametrize(
    "window",
    [
        {"last": 4, "grain": "month", "include_current": True},
        {"to_date": "year"},
        {"start": "2026-01-01", "end": "2026-03-31"},
        {"start": "2026-01-01", "end": "2026-03-31", "grain": "week"},
    ],
)
def test_a_time_filter_value_that_is_a_window_is_stored_as_sent(registry, window):
    from prumi.registry import CheckReport

    registry.save(regional(), HTML)
    sent = CheckReport(filters={"period": window}, flagged=[], canvas=False, errors=[])
    assert registry.record_check("sales", 1, sent).filters == {"period": window}
    assert registry.checks("sales", 1)[0].filters == {"period": window}


@pytest.mark.parametrize(
    "value",
    [
        "Sentinelia",
        12,
        None,
        ["Sentinelia"],
        {"last": 12},  # no grain
        {"last": 0, "grain": "week"},
        {"last": 12, "grain": "fortnight"},
        {"to_date": "week", "note": "Sentinelia"},
        {"start": "2026-03-31", "end": "2026-01-01"},
        {"name": "Sentinelia"},
    ],
)
def test_a_time_filter_value_that_isnt_a_window_is_stored_as_invalid(registry, value):
    from prumi.registry import CheckReport

    registry.save(regional(), HTML)
    wal = registry.path.with_name(registry.path.name + "-wal")
    # While another connection is open, the write stays in the -wal file.
    with closing(sqlite3.connect(registry.path)) as reader:
        reader.execute("SELECT count(*) FROM page_checks").fetchone()
        for region in ([], ["North"]):
            filters = {"period": value, "region": region}
            sent = CheckReport(filters=filters, flagged=[], canvas=True, errors=[])
            registry.record_check("sales", 1, sent)
        stored = registry.path.read_bytes() + wal.read_bytes()
    checks = registry.checks("sales", 1)
    assert [c.filters["period"] for c in checks] == [{"invalid": True}] * 2
    # The state key still hashes the value as sent: each state keeps its own report.
    assert {c.state_key for c in checks} == {
        state_key({"period": value, "region": region}) for region in ([], ["North"])
    }
    assert b'"period":{"invalid":true}' in stored
    assert b"Sentinelia" not in stored and b"fortnight" not in stored


def test_a_report_for_an_unknown_version_is_refused(registry):
    with pytest.raises(KeyError):
        registry.record_check("sales", 1, report([]))
