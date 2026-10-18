"""HTML checks: R8 (no data in the page), bindings, block references, scripts (spec section 8).

R8 can't be fully enforced: an agent can still write numbers into SVG or text. These checks
catch the snapshot pattern; the badge says "certified" only after a human review.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

from prumi.config import DEFAULT_FONT_HOSTS, DEFAULT_SCRIPT_HOSTS, DEFAULT_STYLE_HOSTS
from prumi.issues import Issue, error, warning
from prumi.spec import DashboardSpec

MAX_HTML_BYTES = 2 * 1024 * 1024

R8_HINT = (
    "Every number on the page must come from prumi.on(...) at view time. Remove the data from "
    "the HTML and draw it from the block results; declare total, others or compare in the spec "
    "instead of computing them."
)


@dataclass(frozen=True)
class AllowedHosts:
    script: tuple[str, ...] = DEFAULT_SCRIPT_HOSTS
    style: tuple[str, ...] = DEFAULT_STYLE_HOSTS
    font: tuple[str, ...] = DEFAULT_FONT_HOSTS


DEFAULT_HOSTS = AllowedHosts()


# Parsing

_NOT_VISIBLE = {"script", "style", "template", "noscript", "head", "title"}
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
    "track", "wbr",
}  # fmt: skip


@dataclass
class _Script:
    line: int
    attrs: dict[str, str | None]
    text: str = ""


@dataclass
class _Element:
    tag: str
    line: int
    text: list[str] = field(default_factory=list)
    numeric_cells: int = 0  # tables only


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.scripts: list[_Script] = []
        self.styles: list[tuple[int, str]] = []
        self.links: list[tuple[int, dict[str, str | None]]] = []
        self.texts: list[tuple[int, str]] = []  # each element's own visible text
        self.tables: list[tuple[int, int]] = []  # (line, numeric td cells)
        self._stack: list[_Element] = []
        self._script: _Script | None = None
        self._style: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self.getpos()[0]
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if tag == "script":
            self._script = _Script(line, attributes)
        elif tag == "style":
            self._style = []
        elif tag == "link":
            self.links.append((line, attributes))
        if tag not in _VOID:
            self._stack.append(_Element(tag, line))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append(self._script)
            self._script = None
        elif tag == "style" and self._style is not None:
            self.styles.append((self.getpos()[0], "".join(self._style)))
            self._style = None
        if not any(e.tag == tag for e in self._stack):
            return
        while self._stack:
            element = self._stack.pop()
            self._close(element)
            if element.tag == tag:
                break

    def close(self) -> None:
        super().close()
        while self._stack:
            self._close(self._stack.pop())

    def _close(self, element: _Element) -> None:
        text = " ".join(element.text)
        if text.strip():
            self.texts.append((element.line, text))
        if element.tag == "table":
            self.tables.append((element.line, element.numeric_cells))
        elif element.tag == "td" and _NUMERIC_CELL.fullmatch(text.strip()):
            table = next((e for e in reversed(self._stack) if e.tag == "table"), None)
            if table is not None:
                table.numeric_cells += 1

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.text += data
        elif self._style is not None:
            self._style.append(data)
        elif self._stack and not any(e.tag in _NOT_VISIBLE for e in self._stack):
            self._stack[-1].text.append(data)


def _parse(html: str) -> _Collector:
    collector = _Collector()
    collector.feed(html)
    collector.close()
    return collector


# Checks


def check_html(
    spec: DashboardSpec, html: str, hosts: AllowedHosts = DEFAULT_HOSTS
) -> Iterator[Issue]:
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        yield error(
            "html.too_large",
            "html",
            f"The HTML is {len(html.encode('utf-8')) / 1024 / 1024:.1f} MB; the limit is 2 MB.",
            "Load libraries from an allowed CDN instead of inlining them, and never inline data.",
        )
        return
    page = _parse(html)
    inline = [s for s in page.scripts if not s.attrs.get("src")]
    yield from _embedded_numbers(inline)
    yield from _numbers_in_text(page)
    yield from _bindings(spec, page)
    yield from _block_references(spec, inline)
    yield from _external_urls(page, hosts)


def _embedded_numbers(scripts: list[_Script]) -> Iterator[Issue]:
    for script in scripts:
        is_json = (script.attrs.get("type") or "").strip().lower() in (
            "application/json",
            "application/ld+json",
        )
        found = find_dataset(script.text, is_json)
        if found is not None:
            numbers, line = found
            yield error(
                "R8.embedded_numbers",
                "html",
                f"The inline script on line {script.line + line - 1} holds a literal dataset "
                f"({numbers} numbers in one array or object).",
                R8_HINT,
            )


_DATA_NUMBER = re.compile(
    r"(?<![\w.,])(?P<currency>[$€£¥]\s?)?(?P<number>\d{1,3}(?:,\d{3})+|\d+)(?P<decimals>\.\d+)?"
    r"(?P<suffix>\s?%|[KMBkmb]\b)?"
)
_NUMERIC_CELL = re.compile(r"[-+−]?[$€£¥]?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|[KMBkmb])?")


def data_like_numbers(text: str) -> int:
    """Numbers that look like data: 1,234 / 12.5% / $3.4M / 0.25 (not "2026" or "12")."""
    count = 0
    for match in _DATA_NUMBER.finditer(text):
        if match["currency"] or "," in match["number"] or match["decimals"] or match["suffix"]:
            count += 1
    return count


def _numbers_in_text(page: _Collector) -> Iterator[Issue]:
    for line, text in page.texts:
        if data_like_numbers(text) >= 5:
            yield warning(
                "R8.numbers_in_text",
                "html",
                f"The text of the element on line {line} has {data_like_numbers(text)} numbers "
                "that look like data. Numbers typed into the page go stale and aren't checked.",
                R8_HINT,
            )
    for line, cells in page.tables:
        if cells >= 5:
            yield warning(
                "R8.numbers_in_table",
                "html",
                f"The table on line {line} has {cells} numeric cells written into the HTML.",
                "Build table rows from a block result in prumi.on(...).",
            )


_ID_SELECTOR = re.compile(r"#[A-Za-z][\w-]*")


def _bindings(spec: DashboardSpec, page: _Collector) -> Iterator[Issue]:
    for selector in spec.presentation.bindings:
        path = f"presentation.bindings[{selector!r}]"
        if not _ID_SELECTOR.fullmatch(selector):
            yield error(
                "html.binding_selector",
                path,
                f"The binding key {selector!r} isn't an id selector.",
                'Bind with "#element-id", the id of an element in the HTML.',
            )
        elif selector[1:] not in page.ids:
            yield error(
                "html.binding_selector",
                path,
                f"No element in the HTML has the id {selector[1:]!r}.",
                f'Add <div id="{selector[1:]}"> to the HTML, or change the binding.',
            )


_PRUMI_CALL = re.compile(r"""prumi\s*\.\s*(on|onError|click)\s*\(\s*(["'`])([^"'`]*)\2""")


def _block_references(spec: DashboardSpec, scripts: list[_Script]) -> Iterator[Issue]:
    blocks = [b.id for b in spec.data]
    subscribed = set()
    for script in scripts:
        for match in _PRUMI_CALL.finditer(script.text):
            method, name = match[1], match[3]
            if method == "on":
                subscribed.add(name)
            if name not in blocks:
                yield error(
                    "html.unknown_block",
                    "html",
                    f"prumi.{method}({name!r}) names a block that doesn't exist "
                    f"(the blocks are: {', '.join(blocks)}).",
                    "Use the id of a block in the spec's data.",
                )
    bound = set(spec.presentation.bindings.values())
    for i, block in enumerate(spec.data):
        if block.id not in bound and block.id not in subscribed:
            yield warning(
                "html.unbound_block",
                f"data[{i}]",
                f"The block {block.id!r} is neither bound to an element nor drawn with "
                f"prumi.on({block.id!r}, ...), so the page never shows it.",
                "Draw it with prumi.on, bind it in presentation.bindings, or remove it.",
            )


_SEMVER = r"\d+\.\d+\.\d+(?:[-+][\w.]+)?"
_EXACT_VERSION = re.compile(rf"@{_SEMVER}(?:/|$)|/{_SEMVER}/")
_CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?["']?([^"')\s;]+)""", re.I)
_FONT_FACE = re.compile(r"@font-face\s*\{([^}]*)\}", re.I)
_CSS_URL = re.compile(r"""url\(\s*["']?([^"')\s]+)""", re.I)


def _host(url: str) -> str | None:
    """The host of an absolute or protocol-relative URL; None for relative or data: URLs."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") or (not parts.scheme and url.startswith("//")):
        return (parts.hostname or "").lower()
    return None


def _external_urls(page: _Collector, hosts: AllowedHosts) -> Iterator[Issue]:
    checks: list[tuple[str, int, str, str]] = []  # (kind, line, url, integrity)
    for script in page.scripts:
        if script.attrs.get("src"):
            checks.append(
                ("script", script.line, script.attrs["src"], script.attrs.get("integrity") or "")
            )
    for line, attrs in page.links:
        rel = (attrs.get("rel") or "").lower().split()
        href = attrs.get("href") or ""
        if "stylesheet" in rel:
            checks.append(("style", line, href, ""))
        elif "preload" in rel and (attrs.get("as") or "").lower() == "font":
            checks.append(("font", line, href, ""))
    for line, css in page.styles:
        checks += [("style", line, url, "") for url in _CSS_IMPORT.findall(css)]
        for block in _FONT_FACE.findall(css):
            checks += [("font", line, url, "") for url in _CSS_URL.findall(block)]

    allowed = {"script": hosts.script, "style": hosts.style, "font": hosts.font}
    setting = {"script": "script_hosts", "style": "style_hosts", "font": "font_hosts"}
    for kind, line, url, integrity in checks:
        host = _host(url)
        if host is None:
            continue
        if host not in allowed[kind]:
            yield error(
                "html.script_host",
                "html",
                f"The {kind} URL {url} on line {line} is on {host}, which isn't an allowed "
                f"{kind} host ({', '.join(allowed[kind])}). The dashboard frame's Content "
                "Security Policy would block it anyway.",
                f"Load it from an allowed CDN, or ask the admin to add the host to {setting[kind]} "
                "in prumi.toml.",
            )
        elif kind == "script" and not _EXACT_VERSION.search(urlsplit(url).path):
            yield warning(
                "html.unpinned_script",
                "html",
                f"The script {url} on line {line} has no exact version"
                + ("" if integrity else " and no integrity attribute")
                + ", so the library can change what's rendered without changing the "
                "dashboard's certification fingerprint.",
                "Pin an exact version in the URL, e.g. chart.js@4.4.1 or /d3/7.9.0/.",
            )


# R8: literal datasets in scripts

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
    | (?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))
    | (?P<string>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)
    | (?P<name>[A-Za-z_$][\w$]*)
    | (?P<number>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
    | (?P<punct>=>|\?\?|\|\||&&|[{}\[\]():,;?=.+\-*/%!<>&|^~])
    | (?P<other>.)
    """,
    re.S | re.X,
)
_VALUE_KINDS = {"string", "name", "number"}
# A `{` after these starts an object literal; elsewhere it starts a block of code.
_OBJECT_AFTER = {"=", "(", ",", ":", "[", "?", "??", "||", "&&", "return", "!"}
# A `[` after these indexes something; elsewhere it starts an array literal.
_KEYWORDS = {"return", "case", "typeof", "in", "of", "else", "do", "yield", "await", "new", "void"}

MIN_NUMBERS = 12
MIN_RECORDS = 6
MIN_RECORD_NUMBERS = 6
MIN_SHARE = 0.4


@dataclass
class _Literal:
    kind: str  # array, object, or other (blocks, parentheses, indexing)
    line: int
    numbers: int = 0
    values: int = 0
    records: int = 0  # elements that are themselves arrays or objects (arrays only)


def find_dataset(code: str, is_json: bool = False) -> tuple[int, int] | None:
    """(numbers, line) of the first literal that looks like a dataset, or None.

    A literal is a dataset if it holds at least 12 numeric literals (nested ones included)
    making up at least 40% of its values, or if it's an array of at least 6 records
    (objects or arrays) with at least 6 numbers at the same share. Object keys aren't values.
    """
    tokens = [
        (m.lastgroup, m.group(), code.count("\n", 0, m.start()) + 1)
        for m in _TOKEN.finditer(code)
        if m.lastgroup not in ("ws", "comment")
    ]
    stack: list[_Literal] = []
    previous: tuple[str, str] | None = None
    for index, (kind, text, line) in enumerate(tokens):
        if text in ("[", "{", "(") and kind == "punct":
            stack.append(_Literal(_open_kind(text, previous, is_json), line))
        elif text in ("]", "}", ")") and kind == "punct":
            if stack:
                closed = stack.pop()
                if closed.kind in ("array", "object"):
                    if _is_dataset(closed):
                        return closed.numbers, closed.line
                    if stack and stack[-1].kind == "array":
                        stack[-1].records += 1
        elif kind in _VALUE_KINDS:
            inner = next((s for s in reversed(stack) if s.kind != "other"), None)
            nxt = tokens[index + 1][1] if index + 1 < len(tokens) else ""
            is_key = inner is not None and inner.kind == "object" and nxt == ":"
            if not is_key:
                for literal in stack:
                    if literal.kind in ("array", "object"):
                        literal.values += 1
                        literal.numbers += kind == "number"
        previous = (kind, text)
    return None


def _open_kind(bracket: str, previous: tuple[str, str] | None, is_json: bool) -> str:
    if bracket == "(":
        return "other"
    if is_json:
        return "array" if bracket == "[" else "object"
    prev_kind, prev_text = previous if previous else ("", "")
    if bracket == "[":
        indexing = prev_kind in ("name", "number", "string") and prev_text not in _KEYWORDS
        return "other" if indexing or prev_text in (")", "]") else "array"
    return "object" if prev_text in _OBJECT_AFTER else "other"


def _is_dataset(literal: _Literal) -> bool:
    share = literal.numbers / literal.values if literal.values else 0.0
    if share < MIN_SHARE:
        return False
    if literal.numbers >= MIN_NUMBERS:
        return True
    return (
        literal.kind == "array"
        and literal.records >= MIN_RECORDS
        and literal.numbers >= MIN_RECORD_NUMBERS
    )
