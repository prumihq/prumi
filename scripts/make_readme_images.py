"""The README's two diagrams, each in a light and a dark version, written to docs/images/.

    uv run python scripts/make_readme_images.py                  # writes the four SVGs
    uv run python scripts/make_readme_images.py --preview out/   # also renders PNGs to look at

Edit the drawing here, not the SVGs, so the light and dark versions stay the same picture. The
README's alt text repeats each image's <title>: change both together.
"""

# ruff: noqa: E501  (SVG markup reads better on one line per element)

from __future__ import annotations

import argparse
import base64
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "docs" / "images"

THEMES = {
    "light": dict(
        text="#1f2328",
        muted="#59636e",
        line="#d1d9e0",
        panel="#f6f8fa",
        agent="#8250df",
        agent_tint="#e9dffb",
        agent_soft="#c8b3f5",
        num="#0969da",
        num_soft="#80ccff",
        ok="#1a7f37",
        ok_tint="#dafbe1",
        on_badge="#ffffff",
    ),
    "dark": dict(
        text="#e6edf3",
        muted="#9198a1",
        line="#3d444d",
        panel="#151b23",
        agent="#bc8cff",
        agent_tint="#2b2140",
        agent_soft="#6e50a8",
        num="#58a6ff",
        num_soft="#1f6feb",
        ok="#3fb950",
        ok_tint="#12261a",
        on_badge="#0d1117",
    ),
}

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"


def head(w, h, title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'font-family="{FONT}" role="img">\n<title>{title}</title>\n'
    )


def check(x, y, color, size=10):
    s = size
    return (
        f'<path d="M{x} {y + s * 0.5} l{s * 0.35} {s * 0.4} l{s * 0.65} {-s * 0.9}" fill="none" '
        f'stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def hero(c):
    bars = [118, 112, 122, 126, 132, 138, 134, 136, 140, 150, 156, 168]
    out = [
        head(
            960,
            380,
            "Prumi: the design comes from your AI agent, every number from your semantic layer, the badge from your data team",
        )
    ]
    # The page, drawn as a wireframe: its shapes are the agent's work.
    out.append(
        f'<rect x="20" y="20" width="440" height="340" rx="12" fill="{c["panel"]}" stroke="{c["agent"]}" stroke-width="2" stroke-dasharray="7 5"/>'
    )
    out.append(f'<rect x="44" y="44" width="170" height="12" rx="6" fill="{c["agent_soft"]}"/>')
    out.append(f'<rect x="44" y="64" width="110" height="8" rx="4" fill="{c["agent_tint"]}"/>')
    # Certified badge
    out.append(
        f'<rect x="306" y="38" width="132" height="30" rx="15" fill="{c["ok_tint"]}" stroke="{c["ok"]}" stroke-width="1.5"/>'
    )
    out.append(check(320, 48, c["ok"]))
    out.append(
        f'<text x="338" y="58" font-size="14" font-weight="600" fill="{c["ok"]}">Certified</text>'
    )
    # KPI tiles
    for x, w, label_w, value, delta in (
        (44, 190, 60, "$2.7M", "+31%"),
        (246, 192, 110, "$284.57", "0%"),
    ):
        out.append(
            f'<rect x="{x}" y="88" width="{w}" height="86" rx="8" fill="none" stroke="{c["agent_soft"]}" stroke-width="1.5"/>'
        )
        out.append(
            f'<rect x="{x + 16}" y="102" width="{label_w}" height="8" rx="4" fill="{c["agent_tint"]}"/>'
        )
        out.append(
            f'<text x="{x + 16}" y="146" font-size="30" font-weight="700" fill="{c["num"]}">{value}</text>'
        )
        out.append(
            f'<text x="{x + 16}" y="164" font-size="12" font-weight="600" fill="{c["num"]}">{delta}</text>'
        )
        out.append(
            f'<rect x="{x + 64}" y="157" width="70" height="7" rx="3.5" fill="{c["agent_tint"]}"/>'
        )
    # Chart tile: the agent draws it, but the bar heights are numbers, so they're blue.
    out.append(
        f'<rect x="44" y="188" width="394" height="148" rx="8" fill="none" stroke="{c["agent_soft"]}" stroke-width="1.5"/>'
    )
    base, top = 318, 214
    for i, label in enumerate(("$300K", "$150K", "$0")):
        y = top + i * (base - top) / 2
        out.append(
            f'<line x1="104" y1="{y}" x2="424" y2="{y}" stroke="{c["line"]}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="96" y="{y + 4}" font-size="12" font-weight="600" text-anchor="end" fill="{c["num"]}">{label}</text>'
        )
    for i, h in enumerate(bars):
        h = h * (base - top) / 175
        out.append(
            f'<rect x="{112 + i * 26}" y="{base - h}" width="16" height="{h}" rx="2" fill="{c["num_soft"]}"/>'
        )

    # Legend: who owns which part of the page.
    entries = (
        (
            "agent",
            "Design by your AI agent",
            (
                "Any HTML it likes: layout, charts, words.",
                "It sees metric definitions and the shape",
                "of the data, never the values.",
            ),
        ),
        (
            "num",
            "Numbers from your semantic layer",
            (
                "Computed by the source at the grain shown.",
                "Prumi renders the page and refuses any",
                "number it didn't deliver.",
            ),
        ),
        (
            "ok",
            "Certified by your data team",
            (
                "A dashboard is a draft until they certify it.",
                "Any change makes it a draft again, and the",
                "page can't fake the badge.",
            ),
        ),
    )
    for i, (key, heading, lines) in enumerate(entries):
        y = 50 + i * 116
        out.append(f'<rect x="496" y="{y - 22}" width="5" height="100" rx="2.5" fill="{c[key]}"/>')
        out.append(
            f'<text x="518" y="{y}" font-size="20" font-weight="700" fill="{c[key]}">{heading}</text>'
        )
        for j, line in enumerate(lines):
            out.append(
                f'<text x="518" y="{y + 28 + j * 22}" font-size="15" fill="{c["muted"]}">{line}</text>'
            )
    out.append("</svg>\n")
    return "\n".join(out)


def box(x, y, w, h, c, stroke, title, sub=(), width=1.5, size=17):
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{c["panel"]}" stroke="{stroke}" stroke-width="{width}"/>'
    ]
    n = 1 + len(sub)
    y0 = y + h / 2 - (n - 1) * 10 + 5
    out.append(
        f'<text x="{x + w / 2}" y="{y0}" font-size="{size}" font-weight="700" text-anchor="middle" fill="{c["text"]}">{title}</text>'
    )
    for i, s in enumerate(sub):
        out.append(
            f'<text x="{x + w / 2}" y="{y0 + 22 + i * 18}" font-size="13" text-anchor="middle" fill="{c["muted"]}">{s}</text>'
        )
    return out


def arrow(x1, y1, x2, y2, color, marker, width=2):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" marker-end="url(#{marker})"/>'


def step(x, y, n, color, c):
    return (
        f'<circle cx="{x}" cy="{y}" r="10" fill="{color}"/>'
        f'<text x="{x}" y="{y + 4.5}" font-size="12.5" font-weight="700" text-anchor="middle" fill="{c["on_badge"]}">{n}</text>'
    )


def label(x, y, lines, color, anchor="middle", size=13, weight="400"):
    return "".join(
        f'<text x="{x}" y="{y + i * 17}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{color}">{s}</text>'
        for i, s in enumerate(lines)
    )


def flow(c):
    out = [
        head(
            960,
            420,
            "How Prumi works: the agent designs over MCP, Prumi gets the numbers from your semantic layer, checks the page and serves it, the data team certifies it",
        )
    ]
    out.append("<defs>")
    for name in ("muted", "agent", "num", "ok"):
        out.append(
            f'<marker id="a-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0 0 L10 5 L0 10 z" fill="{c[name]}"/></marker>'
        )
    out.append("</defs>")

    # Prumi in the middle
    px, py, pw, ph = 335, 40, 290, 280
    out.append(
        f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="12" fill="{c["panel"]}" stroke="{c["text"]}" stroke-width="2.5"/>'
    )
    out.append(
        f'<text x="{px + pw / 2}" y="{py + 40}" font-size="24" font-weight="800" text-anchor="middle" fill="{c["text"]}">Prumi</text>'
    )
    rows = (
        (None, "Catalog of official metrics"),
        ("3", "Checks BI rules R1–R10"),
        (None, "Renders it with real data"),
        (None, "Checks every number shown"),
        (None, "Drafts and certified versions"),
    )
    for i, (n, text) in enumerate(rows):
        y = py + 86 + i * 38
        out.append(
            f'<line x1="{px + 18}" y1="{y - 22}" x2="{px + pw - 18}" y2="{y - 22}" stroke="{c["line"]}"/>'
        )
        if n:
            out.append(step(px + 30, y - 4, n, c["text"], c))
        out.append(f'<text x="{px + 48}" y="{y}" font-size="14" fill="{c["text"]}">{text}</text>')

    # Agent on the left, over MCP
    out += box(
        20,
        130,
        180,
        100,
        c,
        c["agent"],
        "Your AI agent",
        ("Claude Code or", "another MCP client"),
        width=2,
    )
    out.append(
        label(110, 260, ("Gets metric definitions and", "the shape of the data,"), c["agent"])
    )
    out.append(label(110, 294, ("never the values",), c["agent"], weight="700"))
    out.append(arrow(202, 180, 331, 180, c["agent"], "a-agent"))
    out.append(label(266, 170, ("MCP",), c["agent"], size=12, weight="700"))
    out.append(step(224, 132, 1, c["agent"], c))
    out.append(label(240, 136, ("find metrics,",), c["muted"], anchor="start"))
    out.append(label(266, 153, ("learn the shape",), c["muted"]))
    out.append(step(224, 208, 2, c["agent"], c))
    out.append(label(240, 212, ("validate and",), c["muted"], anchor="start"))
    out.append(label(266, 229, ("publish the page",), c["muted"]))

    # Source on the right; the data path is drawn in the number colour
    out += box(770, 40, 170, 70, c, c["line"], "Semantic layer", ("dbt / MetricFlow",))
    out += box(770, 150, 170, 70, c, c["num"], "Warehouse", ("your data",))
    out += box(770, 260, 170, 70, c, c["num"], "Browser", ("viewers",))
    out.append(arrow(629, 75, 766, 75, c["muted"], "a-muted"))
    out.append(label(698, 65, ("metric queries",), c["muted"]))
    out.append(arrow(766, 185, 629, 185, c["num"], "a-num", width=2.5))
    out.append(label(698, 175, ("numbers",), c["num"], weight="700"))
    out.append(arrow(629, 295, 766, 295, c["num"], "a-num", width=2.5))
    out.append(step(658, 280, 4, c["num"], c))
    out.append(label(674, 284, ("dashboard",), c["num"], anchor="start", weight="700"))
    out.append(label(698, 316, ("numbers checked",), c["num"]))
    out.append(
        label(855, 360, ("The data never passes", "through the agent"), c["num"], weight="700")
    )

    # Data team below
    out += box(380, 360, 200, 50, c, c["ok"], "Data team", width=2, size=16)
    out.append(arrow(480, 358, 480, 324, c["ok"], "a-ok"))
    out.append(step(500, 341, 5, c["ok"], c))
    out.append(label(516, 345, ("certifies",), c["ok"], anchor="start", weight="700"))
    out.append("</svg>\n")
    return "\n".join(out)


def preview(out: Path) -> None:
    """Render each SVG as an <img> at README width, on GitHub's page colour for its theme."""
    from playwright.sync_api import sync_playwright

    page_colour = {"light": "#ffffff", "dark": "#0d1117"}
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 400})
        for svg in sorted(IMAGES.glob("*.svg")):
            theme = "dark" if svg.stem.endswith("dark") else "light"
            data = base64.b64encode(svg.read_bytes()).decode()
            page.set_content(
                f'<body style="margin:0;padding:20px;background:{page_colour[theme]}">'
                f'<img src="data:image/svg+xml;base64,{data}" style="width:860px;max-width:100%">'
            )
            page.screenshot(path=str(out / f"{svg.stem}.png"), full_page=True)
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", type=Path, metavar="DIR", help="also render PNGs into DIR")
    args = parser.parse_args()
    for theme, c in THEMES.items():
        (IMAGES / f"prumi-at-a-glance-{theme}.svg").write_text(hero(c))
        (IMAGES / f"how-it-works-{theme}.svg").write_text(flow(c))
    if args.preview:
        preview(args.preview)


if __name__ == "__main__":
    main()
