"""The registry: versions, fingerprints, computed status, delete (spec section 10, AC 17, 18)."""

import datetime as dt
import sqlite3
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


# Publishing (AC 17)


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


# Status and certification (AC 18)


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
        assert conn.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    again = Registry(tmp_path / "data")  # migrations don't run twice
    assert again.dashboard("sales").current_version == 1
    with sqlite3.connect(registry.path) as conn:
        assert conn.execute("SELECT count(*) FROM schema_version").fetchone() == (1,)


def test_two_registries_on_one_file_see_each_other(registry, tmp_path):
    cli = Registry(tmp_path / "data")
    registry.save(spec(), HTML)
    cli.certify("sales", 1, by="admin")
    assert registry.status("sales", 1).status == "certified"
