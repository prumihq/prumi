"""The committed JSON Schema of the dashboard spec.

uv run python -m prumi.spec.schema    # rewrites dashboard-spec.v0.schema.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prumi.spec.models import DashboardSpec

SCHEMA_PATH = Path(__file__).with_name("dashboard-spec.v0.schema.json")


def generate_schema() -> dict[str, Any]:
    schema = DashboardSpec.model_json_schema(by_alias=True)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Prumi dashboard spec, format version 0",
        **{k: v for k, v in schema.items() if k != "title"},
    }


def render_schema() -> str:
    return json.dumps(generate_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    SCHEMA_PATH.write_text(render_schema(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
