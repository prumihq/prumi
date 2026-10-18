"""Spec data -> DashboardSpec, with one `spec.invalid` issue per pydantic error."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from prumi.issues import Issue, error
from prumi.spec import DashboardSpec
from prumi.time import window_form

SCHEMA_HINT = (
    "Fix the field to match the dashboard spec format (spec_version 0); its JSON Schema "
    "lists every field."
)


def parse_spec(data: Any) -> tuple[DashboardSpec | None, list[Issue]]:
    if isinstance(data, DashboardSpec):
        return data, []
    try:
        return DashboardSpec.model_validate(data), []
    except ValidationError as exc:
        return None, [_issue(data, e) for e in exc.errors()]


def _issue(data: Any, e: Mapping[str, Any]) -> Issue:
    path = spec_path(data, e["loc"])
    if e["type"] == "extra_forbidden":
        message = f"Unknown field {path.rsplit('.', 1)[-1]!r}."
    elif e["type"] == "missing":
        message = f"Missing required field {path.rsplit('.', 1)[-1]!r}."
    else:
        message = f"{e['msg']} (got {e['input']!r})." if "input" in e else f"{e['msg']}."
    return error("spec.invalid", path or "spec", message, SCHEMA_HINT)


def spec_path(data: Any, loc: Sequence[str | int]) -> str:
    """A pydantic error location as a spec path, without union tags ("filters[0].default").

    Pydantic puts the tag of a tagged union into the location, right after the union's
    value: a filter's type ("time", "dimension") and a window's form ("last", "to_date",
    "range"). Tags can share a name with a field, so they're recognized by position.
    """
    parts: list[str | int] = []
    node = data
    tag_skipped = False  # at most one tag per union value
    for part in loc:
        if not tag_skipped and _is_tag(parts, node, part):
            tag_skipped = True
            continue
        parts.append(part)
        tag_skipped = False
        if isinstance(part, int) and isinstance(node, list | tuple) and part < len(node):
            node = node[part]
        elif isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            node = None
    return format_path(parts)


def _is_tag(parts: Sequence[str | int], node: Any, part: str | int) -> bool:
    if not isinstance(node, Mapping):
        return False
    if len(parts) == 2 and parts[0] == "filters":
        return part == node.get("type")
    if parts and parts[-1] in ("default", "window"):
        return part == window_form(node)
    return False


def format_path(parts: Sequence[str | int]) -> str:
    text = ""
    for part in parts:
        if isinstance(part, int):
            text += f"[{part}]"
        elif part.startswith("#") or not part.isidentifier():
            text += f"[{part!r}]"
        else:
            text += f".{part}" if text else part
    return text
