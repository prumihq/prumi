"""The HTTP app: Host check, routes and the block API (spec section 12, AC 20, 21)."""

import pytest
from prumi.cli import main
from prumi.server.app import create_app
from prumi.server.guards import allowed_hosts
from starlette.testclient import TestClient

BASE = "/api/dashboards/sales/versions/1"


@pytest.fixture
def services(demo_services, demo_dashboard):
    report, published = demo_services.publish(*demo_dashboard)
    assert report.ok and published.version == 1
    return demo_services


@pytest.fixture
def client(services):
    return TestClient(create_app(services), base_url="http://127.0.0.1:8484")


def post(client, path, body, **kwargs):
    return client.post(path, json=body, **kwargs)


# Guards (AC 20)


@pytest.mark.parametrize("host", ["127.0.0.1:8484", "localhost:8484"])
def test_the_configured_host_is_served(client, host):
    assert client.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.example:8484", "127.0.0.1:9999", "192.168.1.5:8484", "localhost", ""]
)
def test_other_host_headers_get_403(client, host):
    for response in (
        client.get("/", headers={"Host": host}),
        client.post(f"{BASE}/blocks/kpi", json={"filters": {}}, headers={"Host": host}),
    ):
        assert response.status_code == 403


def test_ipv6_loopback_hosts():
    assert allowed_hosts("::1", 8484) == {"[::1]:8484", "localhost:8484"}


def test_no_cors_headers(client):
    response = post(
        client, f"{BASE}/blocks/kpi", {"filters": {}}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 200
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]
    preflight = client.options(f"{BASE}/blocks/kpi", headers={"Origin": "https://evil.example"})
    assert preflight.status_code == 405


def test_posts_need_a_json_content_type(client):
    response = client.post(
        f"{BASE}/blocks/kpi",
        content='{"filters": {}}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415


def test_serve_refuses_a_non_loopback_host(capsys, tmp_path):
    (tmp_path / "prumi.toml").write_text('[server]\nhost = "0.0.0.0"\n[source]\nadapter = "dbt"\n')
    assert main(["serve", "--config", str(tmp_path / "prumi.toml")]) == 1
    assert "isn't a loopback address" in capsys.readouterr().err


# The block API (AC 21)


def test_a_block_with_default_filters(client):
    response = post(client, f"{BASE}/blocks/by_region", {"filters": {}})
    assert response.status_code == 200
    result = response.json()
    assert result["block"] == "by_region"
    assert len(result["rows"]) == 5 and result["others"] and result["total"]
    assert result["window"] == {
        "start": "2026-07-20",
        "end": "2026-10-11",
        "grain": "week",
        "partial": False,
    }
    assert result["freshness"] == {"revenue": "2026-10-14", "gross_margin_pct": "2026-10-14"}


def test_filter_values_change_the_result(client):
    everyone = post(client, f"{BASE}/blocks/kpi", {"filters": {}}).json()
    asia = post(client, f"{BASE}/blocks/kpi", {"filters": {"region": ["Asia Pacific"]}}).json()
    week = post(
        client, f"{BASE}/blocks/kpi", {"filters": {"period": {"last": 1, "grain": "week"}}}
    ).json()
    assert asia["rows"][0]["revenue"] < everyone["rows"][0]["revenue"]
    assert week["window"]["start"] == "2026-10-05"


@pytest.mark.parametrize(
    "path",
    [
        "/api/dashboards/nope/versions/1/blocks/kpi",
        "/api/dashboards/sales/versions/2/blocks/kpi",
        f"{BASE}/blocks/nope",
        "/api/dashboards/sales/versions/one/blocks/kpi",
    ],
)
def test_unknown_ids_are_404(client, path):
    assert post(client, path, {"filters": {}}).status_code == 404


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"filters": {"region": ["Asia Pacific"]}}, None),  # by_region doesn't list region
        ({"filters": {"nope": []}}, None),
        ({"filters": {"period": "last 3 weeks"}}, "filter.invalid_value"),
        ({"filters": {"period": {"last": 0, "grain": "week"}}}, "filter.invalid_value"),
        ({"filters": []}, "filter.invalid_value"),
        (["period"], "filter.invalid_value"),
        (
            {"filters": {"period": {"start": "2026-01-15", "end": "2026-02-10", "grain": "month"}}},
            "R3.window_too_short",
        ),
    ],
)
def test_bad_requests_are_400_with_issues(client, body, code):
    response = post(client, f"{BASE}/blocks/by_region", body)
    assert response.status_code == 400
    [issue] = response.json()["issues"]
    assert issue["code"] == (code or "filter.not_listed")
    assert issue["severity"] == "error" and issue["message"]


@pytest.mark.parametrize("value", ["{{ 7 * 7 }}", "a}", "{"])
def test_braces_in_values_are_400(client, value):
    response = post(client, f"{BASE}/blocks/kpi", {"filters": {"region": [value]}})
    assert response.status_code == 400
    assert response.json()["issues"][0]["code"] == "filter.invalid_value"


def test_a_value_with_a_quote_is_fine(client):
    response = post(client, f"{BASE}/blocks/kpi", {"filters": {"region": ["Côte d'Ivoire"]}})
    assert response.status_code == 200


# Status and filter values


def test_status(client, services):
    assert client.get(f"{BASE}/status").json() == {
        "status": "draft",
        "version": 1,
        "current_version": 1,
        "certified_by": None,
        "certified_at": None,
        "fingerprint": services.registry.version("sales", 1).fingerprint,
    }
    services.registry.certify("sales", 1, by="ana")
    certified = client.get(f"{BASE}/status").json()  # read on every request
    assert (certified["status"], certified["certified_by"]) == ("certified", "ana")
    assert client.get("/api/dashboards/sales/versions/7/status").status_code == 404


def test_filter_values(client):
    response = client.get(f"{BASE}/filters/region/values")
    assert response.status_code == 200
    values = response.json()["values"]
    assert values[-1] is None and "Asia Pacific" in values and len(values) == 8
    assert client.get(f"{BASE}/filters/period/values").status_code == 404  # a time filter
    assert client.get(f"{BASE}/filters/nope/values").status_code == 404


def test_index_lists_dashboards(client):
    page = client.get("/").text
    assert '<a href="/d/sales">Sales</a>' in page and "draft" in page
