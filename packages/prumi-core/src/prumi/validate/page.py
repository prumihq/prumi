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
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from contextlib import contextmanager
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from prumi.issues import Issue, error, warning
from prumi.scrub import scrubber
from prumi.spec import DashboardSpec

ORIGIN = "http://prumi.check"
INSTALL = "prumi install-chromium"
SETTLE_MS = 300
TIMEOUT_S = 10.0
MAX_LISTED = 20
# The renders the layout report measures: the default one, and the phone on request.
DEVICES = {"desktop": {"width": 1280, "height": 800}, "phone": {"width": 390, "height": 844}}

UNBOUND_HINT = (
    "Bind this element to the block it shows (presentation.bindings), or move the number into "
    "a bound element. A binding can list several blocks."
)
R8_HINT = (
    "Show only values from prumi.on results, formatted with prumi.format, and compute "
    'nothing you show. Mark axis ticks with data-prumi-role="axis".'
)

# The check's stand-in for the shell: answers the handshake with the dry run's results.
_HOST = """<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>html, body { margin: 0; height: 100%%; overflow: hidden; }</style>
</head><body>
<script type="application/json" id="setup">%s</script>
<iframe id="frame" src="/frame" sandbox="allow-scripts"
        style="display:block;width:100vw;height:100vh;border:0"></iframe>
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
  const delivered = p.fromResults(setup.results, setup.locale);
  const allowed = p.fromData([setup.spec, setup.labels], delivered.allowed, setup.locale);
  // Kept for the hover pass's scans, in this isolated world only.
  window.prumiCheck = { blocks: delivered.blocks, bindings: setup.bindings, allowed,
                        locale: setup.locale };
  const found = p.scan(document, window.prumiCheck, %d);
  found.copied = p.copied(%s, delivered.values, %d, setup.locale);
  found.unreadable = p.unreadable(document);
  return found;
})()"""

# The text of every bound element, by element path, before anything is hovered.
_BOUND_TEXT = """(() => {
  const out = {};
  for (const selector of Object.keys(%s)) {
    let found;
    try { found = document.querySelectorAll(selector); } catch (error) { continue; }
    for (const element of found) out[window.prumiProvenance.path(element)] = element.textContent;
  }
  return out;
})()"""
BROWSER_LOCALE_MESSAGE = (
    "This page formats with the viewer's browser language, so viewers would see different "
    "numbers than the check. Use `prumi.format` for numbers and `prumi.locale` for other "
    "`Intl` calls."
)
BROWSER_LOCALE_HINT = (
    "Never call toLocaleString() or Intl.*(undefined, ...): pass prumi.locale, as in "
    "new Intl.DateTimeFormat(prumi.locale, ...)."
)

# After a hover: let the page draw its tooltip, then scan again.
_RESCAN = """new Promise((resolve) => requestAnimationFrame(() => resolve(
  window.prumiProvenance.scan(document, window.prumiCheck, %d).flagged
)))"""
HOVER_SAMPLES = 20
_HOVER_EVENTS = {"mouseover", "mouseenter", "mousemove", "pointerover", "pointerenter",
                 "pointermove"}  # fmt: skip
_SVG_SHAPES = "rect, path, circle, ellipse, polygon, polyline, line"
TOOLTIP_HINT = (
    "Render the tooltip inside the bound element, or bind it to the blocks it shows "
    "(a binding can list several)."
)

_CSP_URL = re.compile(r"'((?:https?|wss?)://[^']+)'")


class BrowserStartupError(Exception):
    """Headless Chromium can't start, so pages can't be checked."""


class PageChecker:
    """Renders dashboard pages in one shared headless Chromium and reports what they show."""

    def __init__(
        self,
        settle_ms: int = SETTLE_MS,
        timeout_s: float = TIMEOUT_S,
        hover_samples: int = HOVER_SAMPLES,
    ) -> None:
        self.settle_ms = settle_ms
        self.timeout_s = timeout_s
        self.hover_samples = hover_samples
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
        except ImportError as exc:  # pragma: no cover - a dependency of prumi
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
                        f"`{INSTALL}` (`uv run {INSTALL}` in a clone of the repository; add "
                        "--with-deps if system libraries are missing)."
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

    def supports_locale(self, locale: str) -> bool:
        """Whether Chromium formats numbers in `locale`, rather than silently falling back to
        another one."""

        def work(browser) -> bool:
            page = browser.new_page()
            try:
                return page.evaluate(
                    "(tag) => Intl.NumberFormat.supportedLocalesOf([tag]).length > 0", locale
                )
            finally:
                page.close()

        return self._submit(work)

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
        values: Mapping[str, Any] | None = None,
        devices: Sequence[str] = ("desktop",),
        locale: str = "en-US",
        second_render: bool = True,
    ) -> list[Issue]:
        """Issues for what the rendered page shows. `frame_page(origin)` gives the frame's
        HTML and Content-Security-Policy, as the server would serve them. `values` are the
        filter values the page starts with (default: the filters' defaults). The layout is
        measured for each of `devices` (see DEVICES). The browser renders in `locale`, the
        server's display locale, and numbers are read in it. In the default state (no
        `values`), the page is rendered once more in another browser language, and bound
        text that changes with it is R8.browser_locale; `second_render=False` skips that
        (the test suite only)."""
        frame_html, csp = frame_page(ORIGIN)
        setup = {
            "filters": [f.model_dump(mode="json") for f in spec.filters],
            "values": {f.id: f.model_dump(mode="json")["default"] for f in spec.filters}
            | dict(values or {}),
            "labels": dict(labels),
            "formats": dict(formats),
            "bindings": {s: list(b) for s, b in spec.presentation.blocks_of().items()},
            "results": dict(results),
            "spec": spec.model_dump(mode="json", by_alias=True),
            "locale": locale,
        }
        return self._submit(
            lambda browser: self._check(
                browser,
                frame_html,
                csp,
                setup,
                allowed_hosts,
                html,
                devices,
                second_render and values is None,
            )
        )

    @contextmanager
    def _render(self, browser, route, viewport, locale, errors, locate, on_console, scrub=str):
        """A fresh context with the page rendered at `viewport`, in a browser whose language
        is `locale`: yields (page, cdp, evaluate-in-the-isolated-world, settled)."""
        context = browser.new_context(locale=locale, timezone_id="UTC", viewport=viewport)
        try:
            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(_describe_error(exc, locate, scrub)))
            if on_console is not None:
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
            yield page, cdp, evaluate, settled
        finally:
            context.close()

    def _check(
        self, browser, frame_html, csp, setup, allowed_hosts, source_html, devices, languages
    ) -> list[Issue]:
        from prumi.server.assets import read_asset  # prumi.server imports the validator

        setup_json = json.dumps(setup, ensure_ascii=False).replace("<", "\\u003c")
        assets = {
            name: read_asset(name).decode("utf-8")
            for name in ("provenance.js", "runtime.js", "theme.css")
        }
        provenance = read_asset("provenance.js").decode("utf-8")
        errors: list[str] = []
        blocked: list[str] = []
        locate = _locator(source_html, frame_html)
        scrub = _scrubber(setup["results"])

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
                # Only where it went: a URL's path and query can carry data.
                origin = found and "{0.scheme}://{0.netloc}".format(urlsplit(found[1]))
                if origin and origin not in blocked:
                    blocked.append(origin)

        layout_js = files("prumi.validate").joinpath("layout.js").read_text(encoding="utf-8")
        layout_call = f"prumiLayout(document, {json.dumps(setup['bindings'])}, {MAX_LISTED})"
        bound_text = _BOUND_TEXT % json.dumps(setup["bindings"])
        layouts: list[tuple[str, list[dict[str, Any]]]] = []
        shown: dict[str, str] | None = None
        locale = setup["locale"]
        with self._render(
            browser, route, DEVICES["desktop"], locale, errors, locate, on_console, scrub
        ) as (
            page,
            cdp,
            evaluate,
            settled,
        ):
            evaluate(provenance)
            source = json.dumps(source_html).replace("<", "\\u003c")
            scan = evaluate(_SCAN % (setup_json, MAX_LISTED, source, MAX_LISTED))
            scan["unreadable"]["closed_shadow_root"] = _has_closed_shadow_root(cdp)
            if settled and languages:
                shown = evaluate(bound_text)
            if settled and "desktop" in devices:
                layouts.append(("desktop", evaluate(layout_js + ";" + layout_call)))
            if settled and self.hover_samples:
                found = {(f["text"], f["path"]) for f in scan["flagged"]}
                for flagged in _hover_pass(page, cdp, evaluate, setup, self.hover_samples):
                    for item in flagged:
                        if (item["text"], item["path"]) not in found:
                            found.add((item["text"], item["path"]))
                            scan["flagged"].append({**item, "hover": True})
                scan["flagged"] = scan["flagged"][:MAX_LISTED]
        changed: list[str] = []
        if shown is not None:
            # The same state in another browser language: honest pages show the same text.
            with self._render(
                browser, route, DEVICES["desktop"], _other_language(locale), [], locate, None
            ) as (_, _, evaluate, other_settled):
                if other_settled:
                    evaluate(provenance)
                    other = evaluate(bound_text)
                    changed = sorted(p for p in shown.keys() | other.keys()
                                     if shown.get(p) != other.get(p))  # fmt: skip
        for device in devices:
            if device == "desktop":
                continue
            # Another render at the device's size, for the layout only.
            with self._render(browser, route, DEVICES[device], locale, [], locate, None, scrub) as (
                _,
                _,
                evaluate,
                device_settled,
            ):
                if device_settled:
                    layouts.append((device, evaluate(layout_js + ";" + layout_call)))
        return (
            _issues(scan, errors, blocked, settled, self.timeout_s)
            + _locale_issues(changed)
            + _layout_issues(layouts)
        )


def _other_language(locale: str) -> str:
    return "en-US" if locale.lower().split("-")[0] == "de" else "de-DE"


def _locale_issues(changed: Sequence[str]) -> list[Issue]:
    """R8.browser_locale: the element paths whose text changed, never the text."""
    if not changed:
        return []
    listed = ", ".join(changed[:MAX_LISTED])
    more = f" and {len(changed) - MAX_LISTED} more" if len(changed) > MAX_LISTED else ""
    return [
        error(
            "R8.browser_locale",
            "html",
            f"{BROWSER_LOCALE_MESSAGE} Text that changed with the browser's language: "
            f"{listed}{more}.",
            BROWSER_LOCALE_HINT,
        )
    ]


LAYOUT_HINT = {
    "layout.text_overlap": "Give the labels room: fewer or shorter labels, more space, or wrap.",
    "layout.text_clipped": "Let the text wrap, widen its box, or shorten it.",
    "layout.too_wide": "Let content shrink to the screen: max-width: 100%, wrapping, or a "
    "scroll container for wide tables.",
    "layout.small_text": "Use at least 10 px; on charts, fewer ticks rather than smaller ones.",
    "layout.empty_block": "Draw the block in prumi.on, or check its selector and size.",
}


def _where(element: Mapping[str, Any]) -> str:
    b = element["box"]
    return f"{element['path']} (x {b['x']}, y {b['y']}, {b['width']}×{b['height']})"


def _layout_issues(layouts: Sequence[tuple[str, Sequence[Mapping[str, Any]]]]) -> list[Issue]:
    """Layout warnings: element paths and boxes, never the page's text or numbers."""
    issues = []
    for device, found in layouts:
        size = DEVICES[device]
        prefix = "" if device == "desktop" else f"On a phone ({size['width']}×{size['height']}): "
        for item in found:
            code, elements = item["code"], item["elements"]
            where = [_where(e) for e in elements]
            if code == "layout.text_overlap":
                text = f"Text in {where[0]} overlaps text in {where[1]}."
            elif code == "layout.text_clipped":
                cut = "is cut off" if elements[0] == elements[1] else f"is cut off by {where[1]}"
                text = f"Text in {where[0]} {cut}."
            elif code == "layout.too_wide":
                text = f"The page is {item['width']} px wide on a {item['screen']} px screen" + (
                    f"; {where[0]} sticks out." if where else "."
                )
            elif code == "layout.small_text":
                text = f"Text in {where[0]} is {item['size']:g} px, smaller than 10 px."
            else:
                blocks = ", ".join(item.get("blocks", []))
                state = "zero size" if item.get("zero") else "no visible content"
                text = f"{where[0]}, bound to {blocks}, renders with {state}."
            issues.append(warning(code, "html", prefix + text, LAYOUT_HINT[code]))
    return issues


def _hover_pass(page, cdp, evaluate, setup, samples: int):
    """Hover a sample of chart marks and yield what each scan then flags.

    Tooltips often appear only on hover, in an element appended to <body>. The targets are
    elements under bound elements that have hover listeners, then SVG shapes inside bound
    elements (charts often listen on the <svg> and work out the mark from the position),
    taken in turn from each bound element so every chart gets some.
    """
    document = _frame_document(cdp)
    if document is None:
        return
    groups: list[list[dict[str, int]]] = []
    for selector in setup["bindings"]:
        try:
            roots = cdp.send(
                "DOM.querySelectorAll", {"nodeId": document["nodeId"], "selector": selector}
            )["nodeIds"]
        except Exception:  # a selector Chromium doesn't accept binds nothing
            continue
        for root in roots:
            targets: list[dict[str, int]] = []
            node = cdp.send("DOM.resolveNode", {"nodeId": root})["object"]["objectId"]
            listeners = cdp.send(
                "DOMDebugger.getEventListeners", {"objectId": node, "depth": -1, "pierce": True}
            )["listeners"]
            for listener in listeners:
                target = {"backendNodeId": listener.get("backendNodeId")}
                if (
                    listener["type"] in _HOVER_EVENTS
                    and target["backendNodeId"]
                    and (target not in targets)
                ):
                    targets.append(target)
            shapes = cdp.send("DOM.querySelectorAll", {"nodeId": root, "selector": _SVG_SHAPES})
            targets += [{"nodeId": n} for n in shapes["nodeIds"]]
            groups.append(targets)
    picked: list[dict[str, int]] = []
    while len(picked) < samples and any(groups):
        for targets in groups:
            if targets and len(picked) < samples:
                picked.append(targets.pop(0))
    for target in picked:
        try:
            cdp.send("DOM.scrollIntoViewIfNeeded", target)
            quad = cdp.send("DOM.getBoxModel", target)["model"]["content"]
        except Exception:  # not rendered (display: none, removed meanwhile)
            continue
        page.mouse.move(sum(quad[0::2]) / 4, sum(quad[1::2]) / 4)
        yield evaluate(_RESCAN % MAX_LISTED)
    page.mouse.move(0, 0)


def _frame_document(cdp) -> dict | None:
    todo = [cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]]
    while todo:
        node = todo.pop()
        if "contentDocument" in node:
            return node["contentDocument"]
        todo.extend(node.get("children", []))
    return None


_STACK_PLACE = re.compile(r"(https?://[^\s()]+):(\d+):(\d+)")
MAX_STACK_LINES = 5


def _locator(source: str, frame: str) -> Callable[[int, int], tuple[int, int]]:
    """Maps a line and column in the frame document to the submitted HTML.

    Prumi's tags are inserted on one line (the <head> line), so only columns after them on
    that line move.
    """
    start = frame.find(f'<link rel="stylesheet" href="{ORIGIN}/theme.css">')
    shift = len(frame) - len(source)
    if start < 0 or shift <= 0:
        return lambda line, column: (line, column)
    line_at = frame.count("\n", 0, start) + 1
    column_at = start - (frame.rfind("\n", 0, start) + 1) + 1

    def locate(line: int, column: int) -> tuple[int, int]:
        if line == line_at and column > column_at:
            return line, max(column_at, column - shift)
        return line, column

    return locate


def _scrubber(results: Mapping[str, Mapping[str, Any]]) -> Callable[[str], str]:
    """Removes data from text a page produced (an error message): the dimension values it
    was given, and any number with 3+ digits."""
    values = []
    for result in results.values():
        metrics = {c["name"] for c in result.get("columns", []) if c.get("kind") == "metric"}
        for row in result.get("rows", []):
            values += [v for k, v in row.items() if k not in metrics and not k.startswith("_")]
    return scrubber(values)


def _describe_error(
    exc: Any,
    locate: Callable[[int, int], tuple[int, int]],
    scrub: Callable[[str], str] = lambda text: text,
) -> str:
    """ ", at line L, column C of the HTML: <message>", then up to 5 lines of the stack, with
    positions in the submitted HTML ("page") and Prumi's own files by name."""
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    place = ""
    stack = []
    for raw in (getattr(exc, "stack", None) or "").splitlines()[1:]:
        line = raw.strip()
        if not line.startswith("at "):
            continue

        def rewrite(match: re.Match) -> str:
            nonlocal place
            url, row, column = match[1], int(match[2]), int(match[3])
            if url == f"{ORIGIN}/frame":
                row, column = locate(row, column)
                if not place:
                    place = f", at line {row}, column {column} of the HTML"
                return f"page:{row}:{column}"
            return f"{url.rsplit('/', 1)[-1]}:{row}:{column}"

        stack.append(_STACK_PLACE.sub(rewrite, line))
    text = f"{place}: {scrub(message)}"
    if stack:
        text += "\n" + "\n".join(f"  {s}" for s in stack[:MAX_STACK_LINES])
    return text


def _has_closed_shadow_root(cdp) -> bool:
    """Closed shadow roots are invisible to page and isolated-world JavaScript alike; the
    DevTools protocol still sees them."""
    tree = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
    todo = [tree]
    while todo:
        node = todo.pop()
        for shadow in node.get("shadowRoots", []):
            if shadow.get("shadowRootType") == "closed":
                return True
            todo.append(shadow)
        todo.extend(node.get("children", []))
        if "contentDocument" in node:
            todo.append(node["contentDocument"])
    return False


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
                f"The page threw an error while rendering{message}",
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
    found = scan.get("unreadable", {})
    for key, code, what in (
        ("iframe", "html.iframe", "a frame"),
        ("closed_shadow_root", "html.closed_shadow_root", "a closed shadow root"),
        ("image", "html.image", "an image"),
    ):
        if found.get(key):
            issues.append(
                error(
                    code,
                    "html",
                    f"The rendered page has {what}. The number check can't read inside it, so "
                    "pages can't have one.",
                    "Draw icons and charts as inline SVG, keep text as text, and use open "
                    "shadow roots or none.",
                )
            )
    # Company data never reaches the agent: these issues name the element (or the source
    # line) and how many numbers, never the number found.
    lines = sorted({item.get("line") or 0 for item in scan.get("copied", [])})
    for line in lines:
        where = f"on line {line}" if line else "somewhere"
        issues.append(
            error(
                "R8.copied_number",
                "html",
                f"The page's source has a value Prumi delivered {where}: it was copied into "
                "the page, so it won't follow the data.",
                "Remove it, and show the value from the block's result in prumi.on(...).",
            )
        )
    groups: dict[tuple[str, str, bool, str], int] = {}
    for item in scan.get("flagged", []):
        code = item.get("code") or "R8.unverified_number"
        key = (code, item["path"], bool(item.get("hover")), ", ".join(item.get("blocks", [])))
        groups[key] = groups.get(key, 0) + 1
    for (code, where, hover, blocks), count in groups.items():
        numbers = "A number" if count == 1 else f"{count} numbers"
        verb = ("is", "isn't") if count == 1 else ("are", "aren't")
        if code == "R8.unbound_number":
            when = "On hover, " + numbers[0].lower() + numbers[1:] if hover else numbers
            issues.append(
                error(
                    code,
                    "html",
                    f"{when} in {where} {verb[0]} outside every bound element, so "
                    f"{'it' if count == 1 else 'they'} can't be checked against the block "
                    f"{'it comes' if count == 1 else 'they come'} from.",
                    TOOLTIP_HINT if hover else UNBOUND_HINT,
                )
            )
        else:
            issues.append(
                error(
                    code,
                    "html",
                    f"{numbers} in {where} {verb[1]} a value Prumi delivered for the blocks "
                    f"bound there ({blocks}).",
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
