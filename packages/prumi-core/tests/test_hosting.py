"""Frame serving and the shell page (spec section 12, AC 22's server side)."""

import json
import re

import pytest
from prumi.server.pages import frame_html

ORIGIN = "http://127.0.0.1:8484"
RUNTIME = (
    f'<script src="{ORIGIN}/provenance.js"></script><script src="{ORIGIN}/runtime.js"></script>'
)
CSP = (
    "sandbox allow-scripts; default-src 'none'; "
    "script-src http://127.0.0.1:8484 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com "
    "unpkg.com; "
    "style-src 'unsafe-inline' fonts.googleapis.com cdn.jsdelivr.net cdnjs.cloudflare.com "
    "unpkg.com; "
    "font-src data: fonts.gstatic.com cdn.jsdelivr.net cdnjs.cloudflare.com unpkg.com; "
    "img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; "
    "frame-ancestors http://127.0.0.1:8484"
)


# The frame


def test_the_frame_is_sandboxed_with_the_runtime_first_in_head(client, demo_dashboard):
    response = client.get("/d/sales/v/1/frame")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"].startswith("text/html")
    _, html = demo_dashboard
    assert response.text == html.replace("<head>", "<head>" + RUNTIME, 1)


def test_the_origin_follows_the_host_header(client):
    response = client.get("/d/sales/v/1/frame", headers={"Host": "localhost:8484"})
    assert "script-src http://localhost:8484 " in response.headers["content-security-policy"]
    assert '<script src="http://localhost:8484/runtime.js">' in response.text


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            "<html><head><title>x</title></head></html>",
            f"<html><head>{RUNTIME}<title>x</title></head></html>",
        ),
        ('<HEAD lang="en"><p>x', f'<HEAD lang="en">{RUNTIME}<p>x'),
        (
            "<html lang='en'><body><header>x</header></body></html>",
            f"<html lang='en'><head>{RUNTIME}</head><body><header>x</header></body></html>",
        ),
        ("<!DOCTYPE html>\n<p>x</p>", f"<!DOCTYPE html><head>{RUNTIME}</head>\n<p>x</p>"),
        ("<p>x</p>", f"<head>{RUNTIME}</head><p>x</p>"),
    ],
)
def test_runtime_injection(html, expected):
    assert frame_html(html, ORIGIN) == expected


def test_unknown_frames_are_404(client):
    assert client.get("/d/sales/v/2/frame").status_code == 404
    assert client.get("/d/nope/v/1/frame").status_code == 404


# The shell


def shell_config(page: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="prumi-dashboard">(.*?)</script>', page, re.S
    )
    return json.loads(match.group(1))


def test_the_shell_page(client):
    page = client.get("/d/sales").text
    assert '<span id="prumi-badge" data-kind="draft">Draft · not certified</span>' in page
    assert '<iframe id="prumi-frame" src="/d/sales/v/1/frame" sandbox="allow-scripts"' in page
    assert '<script src="/shell.js"></script>' in page
    config = shell_config(page)
    assert config["api"] == "/api/dashboards/sales/versions/1"
    assert [b["id"] for b in config["blocks"]] == ["kpi", "by_region", "trend"]
    assert config["blocks"][1]["filters"] == ["period"]
    assert config["filters"][0] == {
        "id": "period",
        "type": "time",
        "default": {"last": 12, "grain": "week", "include_current": False},
        "calendar": "standard",
    }
    assert config["interactions"] == [{"from": "by_region", "on": "click", "sets_filter": "region"}]
    assert config["labels"]["customer__region"] == "Region"
    assert config["labels"]["average_order_value"] == "Average order value"
    assert config["formats"]["gross_margin_pct"] == {"type": "percent", "unit": None, "decimals": 1}


def test_the_badge_comes_from_the_registry(client, published, demo_dashboard):
    published.registry.certify("sales", 1, by="ana")
    page = client.get("/d/sales").text
    assert re.search(r'data-kind="certified">Certified by ana on \d{4}-\d{2}-\d{2}</span>', page)

    spec, html = demo_dashboard
    published.publish(spec, html + "<!-- edited -->")
    assert 'data-kind="draft">Draft · not certified' in client.get("/d/sales").text
    old = client.get("/d/sales/v/1").text
    assert 'data-kind="old">Version 1 of 2 · not current</span>' in old
    assert 'src="/d/sales/v/1/frame"' in old


def test_the_shell_escapes_what_the_agent_wrote(published, client, demo_dashboard):
    spec, html = demo_dashboard
    spec = {**spec, "dashboard": {"id": "sales", "title": "</script><script>alert(1)</script>"}}
    report, version = published.publish(spec, html)
    assert report.ok, report.issues
    page = client.get("/d/sales").text
    assert "<script>alert(1)" not in page
    assert "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert shell_config(page)  # the JSON block is still intact


def test_unknown_shells_are_404(client):
    assert client.get("/d/nope").status_code == 404
    assert client.get("/d/sales/v/3").status_code == 404
