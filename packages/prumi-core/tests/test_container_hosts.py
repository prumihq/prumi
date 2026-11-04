"""Inside the container the port the browser and the agent use is the host's mapped port, not
Prumi's own: loopback names are answered on any port, and links use the address they came in on."""

import json

import pytest
from prumi.container import ENV
from prumi.server.app import create_app
from starlette.testclient import TestClient

LOCAL_ONLY_NOTE = "This link only works on this machine."


@pytest.fixture
def container_services(monkeypatch, tmp_path, make_demo_config, suite_services, demo_source):
    """Demo services loaded as the image loads them: PRUMI_CONTAINER=1 and host left unset."""
    monkeypatch.setenv(ENV, "1")
    services = suite_services(make_demo_config(tmp_path))
    services.source = demo_source
    assert services.config.server.host == "0.0.0.0"
    return services


@pytest.fixture
def container_client(container_services):
    return TestClient(create_app(container_services), base_url="http://127.0.0.1:8484")


@pytest.fixture
def outside_client(monkeypatch, published):
    monkeypatch.delenv(ENV, raising=False)
    return TestClient(create_app(published), base_url="http://127.0.0.1:8484")


def post_mcp(client: TestClient, host: str, method: str, params: dict):
    """One JSON-RPC POST to /mcp (stateless, so no session first) under the given Host."""
    with client:  # runs the lifespan, which starts the SDK's session manager
        return client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={
                "Host": host,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            },
        )


def publish_over_mcp(client: TestClient, host: str, spec, html) -> dict:
    arguments = {"spec": spec, "html": html}
    response = post_mcp(
        client, host, "tools/call", {"name": "publish_dashboard", "arguments": arguments}
    )
    assert response.status_code == 200, response.text
    body = response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        body = next(line[5:] for line in body.splitlines() if line.startswith("data:"))
    result = json.loads(body)["result"]
    assert not result.get("isError"), result
    return result["structuredContent"]


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8484",
        "localhost:8484",
        "[::1]:8484",
        "127.0.0.1:9000",
        "LOCALHOST:8484",
        "localhost",
    ],
)
def test_in_a_container_loopback_names_are_answered_on_any_port(container_client, host):
    assert container_client.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:8484",
        "0.0.0.0:8484",
        "192.168.1.5:8484",
        "localhost.evil.example:8484",
        "localhost:9000.evil.example",
        "[::1]evil:8484",
        "",
    ],
)
def test_in_a_container_other_host_names_are_refused(container_client, host):
    assert container_client.get("/", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("host", ["localhost:9000", "[::1]:9000", "127.0.0.1:9000", "localhost"])
def test_in_a_container_the_mcp_endpoint_answers_loopback_names_on_any_port(container_client, host):
    assert post_mcp(container_client, host, "tools/list", {}).status_code == 200


def test_in_a_container_the_mcp_endpoint_refuses_other_origins(container_client):
    with container_client:
        response = container_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Host": "127.0.0.1:9000",
                "Origin": "https://evil.example",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert response.status_code == 403


def test_outside_a_container_the_host_check_is_unchanged(outside_client):
    assert outside_client.get("/", headers={"Host": "127.0.0.1:9000"}).status_code == 403
    assert outside_client.get("/", headers={"Host": "127.0.0.1:8484"}).status_code == 200


def test_in_a_container_the_home_page_shows_the_mcp_url_the_browser_used(container_client):
    page = container_client.get("/", headers={"Host": "127.0.0.1:9000"}).text
    assert "http://127.0.0.1:9000/mcp" in page
    assert "0.0.0.0" not in page


def test_in_a_container_published_links_use_the_agents_address(container_client, demo_dashboard):
    published = publish_over_mcp(container_client, "127.0.0.1:9000", *demo_dashboard)
    assert published["url"] == "http://127.0.0.1:9000/d/sales"
    assert published["note"] == LOCAL_ONLY_NOTE


def test_outside_a_container_published_links_are_unchanged(outside_client, demo_dashboard):
    published = publish_over_mcp(outside_client, "127.0.0.1:8484", *demo_dashboard)
    assert published["url"].startswith("http://127.0.0.1:8484/d/")
    assert published["note"] == LOCAL_ONLY_NOTE
