"""The committed JSON Schema of the dashboard spec, and its copy in the authoring guide.

uv run python -m prumi.spec.schema    # rewrites both
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from prumi.spec.models import DashboardSpec

SCHEMA_PATH = Path(__file__).with_name("dashboard-spec.v0.schema.json")
GUIDE_PATH = Path(__file__).parents[1] / "mcp" / "authoring_guide.md"
_GUIDE_BLOCK = re.compile(r"(<!-- schema:start -->\n).*?(<!-- schema:end -->)", re.S)


def generate_schema() -> dict[str, Any]:
    schema = DashboardSpec.model_json_schema(by_alias=True)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Prumi dashboard spec, format version 0",
        **{k: v for k, v in schema.items() if k != "title"},
    }


def render_schema() -> str:
    return json.dumps(generate_schema(), indent=2, ensure_ascii=False) + "\n"


def render_guide(guide: str, schema: str) -> str:
    """The guide with `schema` inlined between its schema markers."""
    return _GUIDE_BLOCK.sub(lambda m: f"{m[1]}```json\n{schema}```\n{m[2]}", guide)


def main() -> None:
    schema = render_schema()
    SCHEMA_PATH.write_text(schema, encoding="utf-8")
    GUIDE_PATH.write_text(render_guide(GUIDE_PATH.read_text(encoding="utf-8"), schema), "utf-8")
    print(f"wrote {SCHEMA_PATH} and the schema in {GUIDE_PATH}")


if __name__ == "__main__":
    main()
