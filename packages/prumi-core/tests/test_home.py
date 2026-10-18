"""The home page: source and freshness, dashboard cards, metric requests; no metric values."""

import dataclasses
import datetime as dt
import re

import pytest
from prumi.registry import PageCheck, Registry
from prumi.server import pages
from prumi.spec import DashboardSpec
from starlette.testclient import TestClient

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
HTML = "<!doctype html><html><body><p id='kpi'></p></body></html>"


def spec(dashboard_id, title, metrics=("revenue",)):
    return DashboardSpec.model_validate(
        {
            "spec_version": 0,
            "dashboard": {"id": dashboard_id, "title": title},
            "data": [
                {
                    "id": "kpi",
                    "metrics": list(metrics),
                    "window": {"last": 4, "grain": "week"},
                    "filters": [],
                }
            ],
            "presentation": {"bindings": {"#kpi": "kpi"}},
        }
    )


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock(dt.datetime(2026, 10, 15, 9, 0, tzinfo=dt.UTC))


@pytest.fixture
def services(demo_services, clock, tmp_path):
    demo_services.registry = Registry(tmp_path / "home", now=clock)
    return demo_services


@pytest.fixture
def client(services):
    from prumi.server.app import create_app

    return TestClient(create_app(services), base_url="http://127.0.0.1:8484")


def cards(page):
    """Each card's HTML (the last one runs on to the end of the page)."""
    return page.split('<li class="card">')[1:]


def test_the_header_names_the_source_and_its_freshness(client):
    page = client.get("/").text
    assert '<span class="wordmark">Prumi</span>' in page
    # No [source] project: the header leaves the name out. The demo's data ends the day
    # before its pinned "today".
    assert '<p class="source" id="source">dbt · duckdb · data through 2026-10-14</p>' in page


def test_the_header_shows_the_configured_project(services, client):
    source = services.config.sources["default"]
    sources = {"default": dataclasses.replace(source, project="<b>prumi_demo</b>")}
    services.config = dataclasses.replace(services.config, sources=sources)
    page = client.get("/").text
    assert (
        '<p class="source" id="source">'
        "dbt · &lt;b&gt;prumi_demo&lt;/b&gt; · duckdb · data through 2026-10-14</p>"
    ) in page


def test_one_card_per_dashboard_newest_first(services, client, clock):
    registry = services.registry
    registry.save(spec("oldest", "Oldest"), HTML)
    clock.now += dt.timedelta(days=1)
    registry.save(spec("certified", "Certified one", ("revenue", "orders")), HTML)
    registry.certify("certified", 1, by="alice")
    clock.now += dt.timedelta(hours=1)
    registry.save(spec("flagged", "Flagged"), HTML)
    registry.save(spec("flagged", "Flagged", ("orders",)), HTML)  # version 2
    flagged = [{"text": "$1", "path": "p#kpi", "code": "R8.unverified_number"}]
    registry.record_check("flagged", 2, PageCheck({}, flagged, False, []))
    clock.now += dt.timedelta(minutes=5)

    found = cards(client.get("/").text)
    assert [re.search(r'href="/d/([^"]+)"', c)[1] for c in found] == [
        "flagged",
        "certified",
        "oldest",
    ]
    newest, certified, oldest = found
    assert '<a class="card-title" href="/d/flagged">Flagged</a>' in newest
    assert '<span class="badge" data-kind="attention">Needs attention</span>' in newest
    assert "<code>flagged</code> · version 2" in newest
    assert ">updated 5 minutes ago</time>" in newest
    assert '<span class="badge" data-kind="certified">Certified by alice</span>' in certified
    assert ">updated 1 hour ago</time>" in certified
    assert '<span class="badge" data-kind="draft">Draft</span>' in oldest
    # The exact time is in the title; the relative one is what's shown.
    assert 'title="2026-10-15T09:00:00+00:00">updated yesterday</time>' in oldest
    assert '<ul class="labels"><li>Revenue</li><li>Orders</li></ul>' in certified


def test_at_most_four_metric_labels_then_a_count(services, client):
    metrics = ("revenue", "orders", "customers", "cost", "discounts", "revenue")
    services.registry.save(spec("many", "Many", metrics), HTML)
    [card] = cards(client.get("/").text)
    assert (
        '<ul class="labels"><li>Revenue</li><li>Orders</li><li>Customers</li>'
        '<li>Cost of goods sold</li><li class="more" title="Discounts">+1</li></ul>'
    ) in card


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [
        (0, "just now"),
        (59, "just now"),
        (60, "1 minute ago"),
        (7200, "2 hours ago"),
        (86400, "yesterday"),
        (3 * 86400, "3 days ago"),
        (65 * 86400, "2 months ago"),
        (400 * 86400, "1 year ago"),
    ],
)
def test_relative_times(seconds, shown):
    now = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
    then = (now - dt.timedelta(seconds=seconds)).isoformat()
    assert pages.relative_time(then, now) == shown


def test_only_readable_dashboards_appear(services, client, monkeypatch):
    services.registry.save(spec("public", "Public"), HTML)
    services.registry.save(spec("secret", "Secret"), HTML)
    monkeypatch.setattr(Registry, "can_read", lambda self, viewer, d: d.id != "secret")
    page = client.get("/").text
    assert 'href="/d/public"' in page and "secret" not in page.lower()


def test_the_empty_state_shows_the_connect_command(client):
    page = client.get("/").text
    assert "Ask your AI agent to build a dashboard; it appears here as a draft." in page
    assert (
        '<code id="connect-command">'
        "claude mcp add --scope user --transport http prumi http://127.0.0.1:8484/mcp</code>"
    ) in page
    assert 'class="cards"' not in page
    assert "Metric requests" not in page  # none yet


def test_open_metric_requests_newest_first_at_most_ten(services, client, clock):
    for n in range(12):
        clock.now += dt.timedelta(days=1)
        services.registry.request_metric(f"Request {n}", "needed")
    page = client.get("/").text
    shown = re.findall(r"<li><span>(Request \d+)</span>", page)
    assert shown == [f"Request {n}" for n in range(11, 1, -1)]
    assert '<span>Request 11</span> <time datetime="2026-10-27T09:00:00+00:00">2026-10-27' in page
    assert "<p>and 2 more (<code>prumi requests</code>)</p>" in page


def test_a_few_requests_have_no_more_line(services, client):
    services.registry.request_metric("Net revenue", "returns matter")
    page = client.get("/").text
    assert "<h2" in page and "<span>Net revenue</span>" in page and "more (" not in page


def test_agent_written_text_is_escaped(services, client):
    services.registry.save(spec("xss", "<script>alert(1)</script>"), HTML)
    services.registry.request_metric('<img src=x onerror="alert(2)"><b>Net</b>', "why")
    page = client.get("/").text
    assert "<script>alert(1)" not in page and "<img" not in page and "<b>Net" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;</a>" in page
    assert "&lt;img src=x onerror=&quot;alert(2)&quot;&gt;&lt;b&gt;Net&lt;/b&gt;" in page


def test_the_csp_allows_no_inline_script_or_style(services, client):
    services.registry.save(spec("sales", "Sales"), HTML)
    services.registry.request_metric("Net revenue", "why")
    response = client.get("/")
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    page = response.text
    assert "<style" not in page and " style=" not in page
    assert not re.search(r"<script(?![^>]*\ssrc=)", page)  # every script is a file
    assert re.findall(r'<link rel="stylesheet" href="([^"]+)">', page) == [
        "/theme.css",
        "/home.css",
    ]
    assert not re.search(r'(?:src|href)="(?:https?:)?//', page.replace(pages.DOCS_URL, ""))


def test_the_footer_has_the_version_and_the_docs(client):
    from prumi import __version__

    page = client.get("/").text
    assert f'Prumi {__version__} · <a href="{pages.DOCS_URL}">Docs</a>' in page


# Freshness: once at startup, then at most every 10 minutes


def test_freshness_is_cached_for_ten_minutes(demo_services, fake_source, monkeypatch):
    from prumi import services as services_module

    demo_services.source = fake_source
    seconds = [1000.0]
    monkeypatch.setattr(services_module.time, "monotonic", lambda: seconds[0])
    # broken_metric fails, late_metric ends early: the latest of the others wins.
    assert demo_services.data_through() == dt.date(2026, 10, 14)
    asked = len(fake_source.adapter.queries)
    seconds[0] += 599
    assert demo_services.data_through() == dt.date(2026, 10, 14)
    assert len(fake_source.adapter.queries) == asked
    seconds[0] += 1
    demo_services.data_through()
    assert len(fake_source.adapter.queries) == 2 * asked
