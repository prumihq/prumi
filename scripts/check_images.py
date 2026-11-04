"""Run the main and demo container images the way the README says, and check what they promise.

    uv run python scripts/check_images.py --image REF --demo-image REF

The main image, with no config mounted, must refuse to start and name /prumi/prumi.toml. The
demo image, published on loopback, must print its welcome link, serve the home page, publish a
dashboard over MCP with a link to the port it was reached on, refuse a foreign Host header, and
let `docker exec ... prumi certify` record a certifier only with --by. Every container and
volume this script creates is removed at the end, also when a check fails or on Ctrl-C.
Needs `docker` on PATH; nothing is pulled or pushed.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, NoReturn

from mcp import Client

START_TIMEOUT_S = 120
MCP_TIMEOUT_S = 180
WELCOME = "#try-prumi-on-it"
CONFIG_PATH = "/prumi/prumi.toml"

# One KPI on the demo's revenue: enough for validation, the page check and a link.
SPEC: dict[str, Any] = {
    "spec_version": 0,
    "dashboard": {"id": "smoke-test", "title": "Smoke test"},
    "data": [
        {
            "id": "kpi",
            "metrics": ["revenue"],
            "window": {"last": 4, "grain": "week"},
            "filters": [],
        }
    ],
    "presentation": {"bindings": {"#kpi": "kpi"}},
}
HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Smoke test</title></head>
<body>
  <p id="kpi"></p>
  <script>
    prumi.on("kpi", (result) => {
      const row = result.rows[0] || {};
      document.getElementById("kpi").textContent = prumi.format(row.revenue, "revenue");
    });
  </script>
</body>
</html>
"""


class Resources:
    """The containers and volumes this run created, removed by `remove`."""

    def __init__(self) -> None:
        self.prefix = f"prumi-check-{uuid.uuid4().hex[:8]}"
        self.containers: list[str] = []
        self.volumes: list[str] = []

    def name(self, kind: str) -> str:
        made = f"{self.prefix}-{kind}-{len(self.containers) + len(self.volumes) + 1}"
        (self.volumes if kind == "data" else self.containers).append(made)
        return made

    def remove(self) -> None:
        for container in self.containers:
            subprocess.run(["docker", "rm", "-f", "-v", container], capture_output=True)
        for volume in self.volumes:
            subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="the main image, e.g. prumi:dev")
    parser.add_argument("--demo-image", required=True, help="the demo image, e.g. prumi-demo:dev")
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        sys.exit("check_images: needs `docker` on PATH.")
    started = time.monotonic()
    created = Resources()
    try:
        _main_image_needs_a_config(args.image, created)
        container, port = _demo_starts(args.demo_image, created, check=2)
        dashboard_id = _publishes_over_mcp(port, check=3)
        _refuses_a_foreign_host(port)
        _certify_needs_by(container, dashboard_id)
        _, other_port = _demo_starts(args.demo_image, created, check=6)
        _publishes_over_mcp(other_port, check=6)
    except KeyboardInterrupt:
        sys.exit("check_images: interrupted")
    finally:
        _step("7. remove the containers and volumes")
        created.remove()
    print(f"check_images: passed in {time.monotonic() - started:.0f} s")
    return 0


def _main_image_needs_a_config(image: str, created: Resources) -> None:
    _step(f"1. docker run --rm {image} refuses to start without {CONFIG_PATH}")
    # Named, so a run that hangs past the timeout is still found and removed.
    command = ["docker", "run", "--rm", "--name", created.name("main"), image]
    result = _run(command, 1, expect_success=False, timeout=START_TIMEOUT_S)
    if result.returncode == 0:
        _fail(1, f"the main image started without a config (exit 0): {result.stderr}")
    if CONFIG_PATH not in result.stderr:
        _fail(1, f"the main image's error doesn't name {CONFIG_PATH}: {result.stderr!r}")


def _demo_starts(image: str, created: Resources, check: int) -> tuple[str, int]:
    """Runs the demo image published on a free loopback port; its welcome link, then GET /."""
    port = _free_port()
    volume = created.name("data")
    container = created.name("demo")
    _step(f"{check}. docker run the demo image on 127.0.0.1:{port}, GET /")
    _run(["docker", "volume", "create", volume], check)
    _run(
        [
            "docker", "run", "-d", "--name", container,
            "-p", f"127.0.0.1:{port}:8484", "-v", f"{volume}:/data", image,
        ],
        check,
    )  # fmt: skip
    deadline = time.monotonic() + START_TIMEOUT_S
    while True:
        # Running first: a container that prints and exits in between still shows its logs.
        running = _running(container)
        logs = _logs(container)
        if logs:
            break
        if not running:
            _fail(check, "the demo container exited without printing anything")
        if time.monotonic() > deadline:
            _fail(check, f"the demo container printed nothing in {START_TIMEOUT_S} s")
        time.sleep(0.2)
    first = logs.splitlines()[0]
    if WELCOME not in first:
        _fail(check, f"the first log line isn't the welcome line with {WELCOME}: {first!r}")
    while True:
        if not _running(container):
            _fail(check, f"the demo container exited before the home page answered:\n{logs}")
        if _get(f"http://127.0.0.1:{port}/") == 200:
            return container, port
        if time.monotonic() > deadline:
            _fail(check, f"the home page didn't answer 200 in {START_TIMEOUT_S} s")
        time.sleep(0.5)
        logs = _logs(container)


def _publishes_over_mcp(port: int, check: int) -> str:
    """Publishes the smoke-test dashboard over MCP; returns its id once its link answers 200."""
    base = f"http://127.0.0.1:{port}"
    _step(f"{check}. publish_dashboard over MCP at {base}/mcp, GET its link")
    try:
        out = asyncio.run(_publish(f"{base}/mcp"))
    except Exception as e:  # noqa: BLE001 - any failure to talk MCP fails the check
        _fail(check, f"the MCP call failed: {type(e).__name__}: {e}")
    if out.get("status") != "draft":
        _fail(check, f"the published dashboard isn't a draft: {out}")
    url = out.get("url", "")
    if not url.startswith(f"{base}/d/"):
        _fail(check, f"the link doesn't name {base}/d/: {url!r}")
    status = _get(url)
    if status != 200:
        _fail(check, f"GET {url} answered {status}")
    return out["id"]


async def _publish(url: str) -> dict[str, Any]:
    async with Client(url) as mcp:
        result = await mcp.call_tool(
            "publish_dashboard", {"spec": SPEC, "html": HTML}, read_timeout_seconds=MCP_TIMEOUT_S
        )
    if result.is_error:
        raise RuntimeError(f"publish_dashboard returned an error: {result.content}")
    return result.structured_content or {}


def _refuses_a_foreign_host(port: int) -> None:
    _step("4. GET / with Host: evil.example:8484 is refused")
    status = _get(f"http://127.0.0.1:{port}/", host="evil.example:8484")
    if status != 403:
        _fail(4, f"a request with Host: evil.example:8484 answered {status}, not 403")


def _certify_needs_by(container: str, dashboard_id: str) -> None:
    _step(f"5. docker exec prumi certify {dashboard_id} 1, without and with --by")
    certify = ["docker", "exec", container, "prumi", "certify", dashboard_id, "1"]
    refused = _run(certify, 5, expect_success=False)
    output = refused.stdout + refused.stderr
    if refused.returncode == 0 or "--by" not in output:
        _fail(
            5, f"certify without --by didn't fail mentioning --by ({refused.returncode}): {output}"
        )
    _run([*certify, "--by", "Smoke Test"], 5)


def _logs(container: str) -> str:
    # One pipe for both streams keeps the container's own order of lines.
    result = subprocess.run(
        ["docker", "logs", container], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return result.stdout


def _running(container: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "true"


def _get(url: str, host: str | None = None) -> int | None:
    """The status of GET url (None when nothing answers); `host` replaces the Host header."""
    request = urllib.request.Request(url, headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _step(name: str) -> None:
    print(f"check_images: {name}", flush=True)


def _fail(check: int, message: str) -> NoReturn:
    sys.exit(f"check_images: check {check} failed: {message}")


def _run(
    command: list[str], check: int, expect_success: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Runs a command for its output; a timeout, or a non-zero exit when expect_success,
    fails `check`."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _fail(check, f"no exit in {timeout:.0f} s: {' '.join(command)}")
    if expect_success and result.returncode != 0:
        _fail(check, f"exit {result.returncode}: {' '.join(command)}\n{result.stderr}")
    return result


if __name__ == "__main__":
    sys.exit(main())
