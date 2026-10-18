"""Ctrl+C or SIGTERM stops `prumi serve` quickly, with clients connected, and Chromium too."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest
from mcp import Client

pytestmark = pytest.mark.browser

STOP_WITHIN_S = 5


def descendants(pid: int) -> set[int]:
    found, todo = set(), [pid]
    while todo:
        children = subprocess.run(
            ["ps", "-o", "pid=", "--ppid", str(todo.pop())], capture_output=True, text=True
        ).stdout.split()
        for child in map(int, children):
            found.add(child)
            todo.append(child)
    return found


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not stat.stdout.strip().startswith("Z")


@pytest.fixture
def config(live_services, demo_dashboard):
    services = live_services()
    report, _ = services.publish(*demo_dashboard)
    assert report.ok, report.issues
    return services.config


@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM"])
def test_the_server_stops_within_five_seconds(config, name):
    from playwright.sync_api import Error, sync_playwright

    port = config.server.port
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "prumi.cli", "serve", "--config", str(config.path)],
        start_new_session=True,  # its own process group, as in a terminal
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                assert server.poll() is None and time.monotonic() < deadline, "no server"
                time.sleep(0.1)

        # An MCP client stays connected, and a browser has a dashboard open.
        connected, done = threading.Event(), threading.Event()

        async def agent():
            async with Client(f"{url}/mcp") as client:
                await client.list_tools()
                connected.set()
                while not done.is_set():
                    await asyncio.sleep(0.05)

        thread = threading.Thread(target=lambda: asyncio.run(agent()), daemon=True)
        thread.start()
        assert connected.wait(20)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Error:
                if os.environ.get("PRUMI_REQUIRE_BROWSER"):
                    raise
                pytest.skip("Headless Chromium can't start.")
            page = browser.new_page()
            page.goto(f"{url}/d/sales")
            page.frame_locator("#prumi-frame").locator("body").wait_for()
            spawned = descendants(server.pid)  # the server's Playwright driver and Chromium
            assert spawned

            started = time.monotonic()
            if name == "SIGINT":
                os.killpg(server.pid, signal.SIGINT)  # Ctrl+C reaches the whole group
            else:
                server.send_signal(signal.SIGTERM)
            server.wait(STOP_WITHIN_S + 5)
            assert time.monotonic() - started < STOP_WITHIN_S
            browser.close()
        done.set()
        deadline = time.monotonic() + 2
        while any(alive(p) for p in spawned) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not [p for p in spawned if alive(p)], "Chromium outlived the server"
    finally:
        if server.poll() is None:
            os.killpg(server.pid, signal.SIGKILL)
