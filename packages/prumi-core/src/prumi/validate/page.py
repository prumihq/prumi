"""The server's page check: render the dashboard in headless Chromium and read its numbers.

The page gets the dry run's block results through the same postMessage protocol the shell
uses, so checking runs no extra queries. Once rendering settles, the matcher from
provenance.js reads every number shown, from an isolated world: page code shares the DOM
with it but none of its JavaScript, so it can't switch the scan off or feed it values.

One Chromium serves the whole process. Checks run one at a time, each in a fresh browser
context, on a thread of their own (Playwright's sync API stays on the thread that started it).
"""

from __future__ import annotations

import atexit
import json
import queue
import re
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from typing import Any
from urllib.parse import urlsplit

from prumi.issues import Issue, error, warning
from prumi.spec import DashboardSpec

ORIGIN = "http://prumi.check"
INSTALL = "uv run playwright install chromium"
SETTLE_MS = 300
TIMEOUT_S = 10.0
MAX_LISTED = 20

R8_HINT = (
    "Show only values from prumi.on results, formatted with prumi.format, and compute "
    'nothing you show. Mark axis ticks with data-prumi-role="axis".'
)

# The check's stand-in for the shell: answers the handshake with the dry run's results.
_HOST = """<!doctype html><html><body>
<script type="application/json" id="setup">%s</script>
<iframe id="frame" src="/frame" sandbox="allow-scripts"
        style="width:1280px;height:900px;border:0"></iframe>
<script>
  var setup = JSON.parse(document.getElementById("setup").textContent);
  var frame = document.getElementById("frame");
  window.prumiDelivered = false;
  function post(type, payload) {
    frame.contentWindow.postMessage(Object.assign({ prumi: 0, type: type }, payload), "*");
  }
  window.addEventListener("message", function (event) {
    var m = event.data;
    if (event.source !== frame.contentWindow || !m || m.prumi !== 0) return;
    if (m.type === "ready" && !window.prumiDelivered) {
      post("init", { filters: setup.filters, values: setup.values, labels: setup.labels,
                     formats: setup.formats, bindings: setup.bindings });
      (m.blocks || []).forEach(function (id) {
        if (setup.results[id]) post("data", { block: id, result: setup.results[id] });
      });
      window.prumiDelivered = true;
    } else if (m.type === "filterValues") {
      post("filterValues", { request: m.request, values: [] });
    }
  });
</script>
</body></html>"""

# Resolves "settled" once the DOM hasn't changed for `quiet` ms, or "timeout".
_SETTLE = """new Promise((resolve) => {
  let timer;
  const finish = (how) => { observer.disconnect(); clearTimeout(timer); resolve(how); };
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => finish("settled"), %(quiet)d);
  });
  observer.observe(document, { subtree: true, childList: true, characterData: true,
                               attributes: true });
  timer = setTimeout(() => finish("settled"), %(quiet)d);
  setTimeout(() => finish("timeout"), %(timeout)d);
})"""

_SCAN = """(() => {
  const p = window.prumiProvenance;
  const setup = %s;
  const delivered = p.fromResults(setup.results);
  const allowed = p.fromData([setup.spec, setup.labels], delivered.allowed);
  const found = p.scan(document, { values: delivered.values, allowed }, %d);
  found.copied = p.copied(%s, delivered.values, %d);
  return found;
})()"""

_CSP_URL = re.compile(r"'((?:https?|wss?)://[^']+)'")


class BrowserStartupError(Exception):
    """Headless Chromium can't start, so pages can't be checked."""


class PageChecker:
    """Renders dashboard pages in one shared headless Chromium and reports what they show."""

    def __init__(self, settle_ms: int = SETTLE_MS, timeout_s: float = TIMEOUT_S) -> None:
        self.settle_ms = settle_ms
        self.timeout_s = timeout_s
        self._jobs: queue.Queue[tuple[Callable[[Any], Any], Future] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started: Future = Future()
        self._lock = threading.Lock()

    # Lifecycle

    def start(self) -> None:
        """Launch Chromium now (it's launched on the first check otherwise)."""
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="prumi-chromium", daemon=True
                )
                self._thread.start()
        self._started.result()

    def close(self) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            self._jobs.put(None)
            thread.join(10)

    def _run(self) -> None:
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError as exc:  # pragma: no cover - a dependency of prumi-core
            self._started.set_exception(BrowserStartupError(f"Playwright isn't installed: {exc}"))
            return
        try:
            playwright = sync_playwright().start()
        except Exception as exc:  # pragma: no cover
            self._started.set_exception(BrowserStartupError(f"Playwright can't start: {exc}"))
            return
        try:
            try:
                browser = playwright.chromium.launch()
            except Error as exc:
                reason = str(exc).strip().splitlines()[0]
                self._started.set_exception(
                    BrowserStartupError(
                        f"Headless Chromium can't start ({reason}). Prumi renders every "
                        "dashboard to check its numbers, so it needs Chromium: run "
                        f"`{INSTALL}` (add --with-deps if system libraries are missing)."
                    )
                )
                return
            self._started.set_result(None)
            while (job := self._jobs.get()) is not None:
                work, result = job
                if result.set_running_or_notify_cancel():
                    try:
                        result.set_result(work(browser))
                    except BaseException as exc:  # handed to the caller
                        result.set_exception(exc)
            browser.close()
        finally:
            playwright.stop()

    def _submit(self, work: Callable[[Any], Any]) -> Any:
        self.start()
        result: Future = Future()
        self._jobs.put((work, result))
        return result.result()

    # The check

    def check(
        self,
        spec: DashboardSpec,
        html: str,
        results: Mapping[str, dict[str, Any]],
        labels: Mapping[str, str],
        formats: Mapping[str, Any],
        frame_page: Callable[[str], tuple[str, str]],
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> list[Issue]:
        """Issues for what the rendered page shows. `frame_page(origin)` gives the frame's
        HTML and Content-Security-Policy, as the server would serve them."""
        frame_html, csp = frame_page(ORIGIN)
        setup = {
            "filters": [f.model_dump(mode="json") for f in spec.filters],
            "values": {f.id: f.model_dump(mode="json")["default"] for f in spec.filters},
            "labels": dict(labels),
            "formats": dict(formats),
            "bindings": dict(spec.presentation.bindings),
            "results": dict(results),
            "spec": spec.model_dump(mode="json", by_alias=True),
        }
        return self._submit(
            lambda browser: self._check(browser, frame_html, csp, setup, allowed_hosts, html)
        )

    def _check(self, browser, frame_html, csp, setup, allowed_hosts, source_html) -> list[Issue]:
        from prumi.server.assets import read_asset  # prumi.server imports the validator

        setup_json = json.dumps(setup, ensure_ascii=False).replace("<", "\\u003c")
        assets = {
            name: read_asset(name).decode("utf-8")
            for name in ("provenance.js", "runtime.js", "theme.css")
        }
        provenance = read_asset("provenance.js").decode("utf-8")
        errors: list[str] = []
        blocked: list[str] = []

        def route(r) -> None:
            url = r.request.url
            parts = urlsplit(url)
            if f"{parts.scheme}://{parts.netloc}" == ORIGIN:
                path = parts.path
                if path == "/":
                    return r.fulfill(body=_HOST % setup_json, content_type="text/html")
                if path == "/frame":
                    return r.fulfill(
                        body=frame_html,
                        content_type="text/html",
                        headers={"Content-Security-Policy": csp},
                    )
                name = path.lstrip("/")
                if name in assets:
                    kind = "text/css" if name.endswith(".css") else "text/javascript"
                    return r.fulfill(body=assets[name], content_type=kind)
                return r.abort()
            if (parts.hostname or "") in allowed_hosts:
                return r.continue_()  # libraries from the allowed CDNs, as viewers load them
            return r.abort()

        def on_console(message) -> None:
            if message.type == "error" and "Content Security Policy" in message.text:
                found = _CSP_URL.search(message.text)
                if found and found[1] not in blocked:
                    blocked.append(found[1])

        context = browser.new_context(
            locale="en-US", timezone_id="UTC", viewport={"width": 1280, "height": 900}
        )
        try:
            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc) or type(exc).__name__))
            page.on("console", on_console)
            page.goto(f"{ORIGIN}/", wait_until="load", timeout=self.timeout_s * 1000)
            frame = page.frame(url=f"{ORIGIN}/frame")
            cdp = context.new_cdp_session(page)
            world = cdp.send(
                "Page.createIsolatedWorld",
                {"frameId": _frame_id(cdp, f"{ORIGIN}/frame"), "worldName": "prumi-check"},
            )["executionContextId"]

            def evaluate(expression: str) -> Any:
                answer = cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "contextId": world,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                )
                if "exceptionDetails" in answer:
                    raise RuntimeError(answer["exceptionDetails"].get("text", "scan failed"))
                return answer["result"].get("value")

            timeout_ms = int(self.timeout_s * 1000)
            delivered = True
            try:
                page.wait_for_function("window.prumiDelivered === true", timeout=timeout_ms)
            except Exception:
                delivered = False
            settled = (
                delivered
                and frame is not None
                and (
                    evaluate(_SETTLE % {"quiet": self.settle_ms, "timeout": timeout_ms})
                    == "settled"
                )
            )
            evaluate(provenance)
            source = json.dumps(source_html).replace("<", "\\u003c")
            scan = evaluate(_SCAN % (setup_json, MAX_LISTED, source, MAX_LISTED))
        finally:
            context.close()
        return _issues(scan, errors, blocked, settled, self.timeout_s)


def _frame_id(cdp, url: str) -> str:
    def find(tree):
        if tree["frame"].get("url") == url:
            return tree["frame"]["id"]
        for child in tree.get("childFrames", []):
            if (found := find(child)) is not None:
                return found
        return None

    found = find(cdp.send("Page.getFrameTree")["frameTree"])
    if found is None:
        raise RuntimeError("the dashboard frame didn't load")
    return found


def _issues(
    scan: Mapping[str, Any], errors: list[str], blocked: list[str], settled: bool, timeout: float
) -> list[Issue]:
    issues = []
    if not settled:
        issues.append(
            error(
                "html.render_timeout",
                "html",
                f"The page didn't finish rendering within {timeout:g} s (it kept changing, or "
                "never received its data), so its numbers couldn't be checked.",
                "Stop timers and animations that keep changing the page once it has drawn its "
                "data.",
            )
        )
    for message in dict.fromkeys(errors):
        issues.append(
            error(
                "html.runtime_error",
                "html",
                f"The page threw an error while rendering: {message}",
                "Fix the script; check every field you read from a result exists, and every "
                "element you write to.",
            )
        )
    if scan.get("canvas"):
        issues.append(
            error(
                "html.canvas",
                "html",
                "The page uses <canvas>. Text drawn into a canvas can't be read, so its numbers "
                "can't be checked.",
                "Draw charts with SVG or HTML elements instead.",
            )
        )
    for item in scan.get("copied", []):
        line = f"on line {item['line']}" if item.get("line") else "on a line"
        issues.append(
            error(
                "R8.copied_number",
                "html",
                f"The page's source has {item['text']} ({line}), a value Prumi delivered: it "
                "was copied into the page, so it won't follow the data.",
                "Remove it, and show the value from the block's result in prumi.on(...).",
            )
        )
    for item in scan.get("flagged", []):
        issues.append(
            error(
                "R8.unverified_number",
                "html",
                f"The page shows {item['text']} (in {item['path']}), which isn't a value "
                "Prumi delivered.",
                R8_HINT,
            )
        )
    for url in blocked:
        issues.append(
            warning(
                "html.blocked_request",
                "html",
                f"The page tried to reach {url}; the dashboard frame blocks every network call.",
                "Data only comes through prumi.on. Load libraries from an allowed host.",
            )
        )
    return issues


_shared: PageChecker | None = None
_shared_lock = threading.Lock()


def shared_checker() -> PageChecker:
    """The process's checker: one Chromium per server process."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = PageChecker()
            atexit.register(_shared.close)
        return _shared
