"""Spec data -> DashboardSpec, with one `spec.invalid` issue per problem.

Messages say what each field must be, in the spec's terms, never in pydantic's ("tuple",
"model", "union", "dict", "Input should be"): agents read them to fix the spec (spec 18).
"""

from __future__ import annotations

import re
import types
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from prumi.issues import Issue, error
from prumi.spec import Block, DashboardSpec, DimensionFilter, Interaction, TimeFilter
from prumi.time import LastWindow, RangeWindow, ToDateWindow, window_form

SCHEMA_HINT = (
    "Fix the field to match the dashboard spec format (spec_version 0); authoring_guide "
    "describes every field and has an example."
)

# Tagged-union members, by the tag pydantic puts into an error's location.
_TAGGED = {
    "time": TimeFilter,
    "dimension": DimensionFilter,
    "last": LastWindow,
    "to_date": ToDateWindow,
    "range": RangeWindow,
}
# Members of a plain union (a filter value) that pydantic names in the location.
_SCALARS = {"str", "int", "float", "bool", "none"}

# "<field> must be a list of <noun>", by the list's field name.
_LIST_NOUNS = {
    ("", "filters"): "filters",
    ("", "data"): "blocks",
    ("", "interactions"): "interactions",
    ("", "metric_requests"): "metric request ids",
    ("block", "metrics"): "metric names",
    ("block", "dimensions"): "dimension names",
    ("block", "filters"): "filter ids",
    ("filter", "default"): "values",
}

_PATTERNS = {
    r"^[a-z0-9][a-z0-9-]{0,62}$": (
        "lowercase letters, digits and hyphens, starting with a letter or digit, "
        "at most 63 characters"
    ),
    r"^[a-z0-9][a-z0-9_-]{0,62}$": (
        "lowercase letters, digits, hyphens and underscores, starting with a letter or "
        "digit, at most 63 characters"
    ),
}


def parse_spec(data: Any) -> tuple[DashboardSpec | None, list[Issue]]:
    if isinstance(data, DashboardSpec):
        return data, []
    try:
        return DashboardSpec.model_validate(data), []
    except ValidationError as exc:
        return None, plain_issues(exc, data, DashboardSpec)


def plain_issues(exc: ValidationError, data: Any, root: type[BaseModel]) -> list[Issue]:
    """One spec.invalid issue per problem in `exc`, which came from validating `data`."""
    issues = []
    for e in exc.errors():
        loc = tuple(e["loc"])
        scalar_union = any(part in _SCALARS for part in loc)
        if scalar_union:
            loc = loc[: next(i for i, part in enumerate(loc) if part in _SCALARS)]
        path = spec_path(data, loc)
        message = (
            f"{_name(path)} must be text, a number, true or false, or null"
            if scalar_union
            else describe(e, path, loc, root)
        )
        issues.append(error("spec.invalid", path or "spec", message + ".", SCHEMA_HINT))
    return list(dict.fromkeys(issues))


def describe(e: Mapping[str, Any], path: str, loc: Sequence[str | int], root: type) -> str:
    """What the field must be, in the spec's words."""
    kind, ctx, name = e["type"], e.get("ctx") or {}, _name(path)
    if kind == "missing":
        return f"{name} is required"
    if kind == "extra_forbidden":
        parent = _annotation_at(root, loc[:-1])
        parent_path = path.rpartition(".")[0] if "." in path else ""
        owner = f"`{parent_path}`" if parent_path else "the spec"
        fields = _fields(parent) if _is_model(parent) else []
        listed = f"; its fields are {_ticks(fields)}" if fields else ""
        return f"{name} isn't a field of {owner}{listed}"
    if kind in ("tuple_type", "list_type"):
        return f"{name} must be a list of {_list_noun(root, loc)}"
    if kind in ("model_type", "model_attributes_type", "dataclass_type"):
        expected = _annotation_at(root, loc)
        fields = _fields(expected) if _is_model(expected) else []
        with_fields = f" with the fields {_ticks(fields)}" if fields else ""
        return f"{name} must be an object{with_fields}"
    if kind in ("dict_type", "mapping_type"):
        return f"{name} must be an object"
    if kind == "literal_error":
        choices = _literal_choices(_annotation_at(root, loc)) or _quoted(ctx.get("expected", ""))
        if len(choices) == 1:
            return f"{name} must be `{choices[0]}`"
        return f"{name} must be one of {_ticks(choices)}"
    if kind in ("int_type", "int_parsing", "int_from_float"):
        return f"{name} must be a whole number"
    if kind in ("float_type", "float_parsing", "finite_number"):
        return f"{name} must be a number"
    if kind in ("bool_type", "bool_parsing"):
        return f"{name} must be true or false"
    if kind == "string_type":
        return f"{name} must be text"
    if kind.startswith("date"):
        return f"{name} must be a date like `2026-01-31`"
    if kind == "greater_than_equal":
        return f"{name} must be at least {ctx['ge']}"
    if kind == "less_than_equal":
        return f"{name} must be at most {ctx['le']}"
    if kind == "too_short":
        n = ctx["min_length"]
        return f"{name} must have at least {n} item{'s' * (n != 1)}"
    if kind == "too_long":
        n = ctx["max_length"]
        return f"{name} must have at most {n} item{'s' * (n != 1)}"
    if kind == "string_too_short":
        n = ctx["min_length"]
        return f"{name} can't be empty" if n == 1 else f"{name} must have at least {n} characters"
    if kind == "string_too_long":
        return f"{name} must have at most {ctx['max_length']} characters"
    if kind == "string_pattern_mismatch":
        rule = _PATTERNS.get(ctx.get("pattern", ""))
        return f"{name} must be {rule}" if rule else f"{name} doesn't have the expected form"
    if kind in ("union_tag_invalid", "union_tag_not_found"):
        tags = _quoted(ctx.get("expected_tags", "")) or ["time", "dimension"]
        field = _name(f"{path}.type" if path else "type")
        verb = "must be" if kind == "union_tag_invalid" else "is required, and must be"
        return f"{field} {verb} one of {_ticks(tags)}"
    if kind == "window_form":
        return (
            f"{name} must be a window: `{{last: N, grain: G}}`, `{{to_date: G}}` or "
            "`{start: D1, end: D2}`"
        )
    if kind == "value_error":
        return f"{name}: {e['msg'].removeprefix('Value error, ')}"
    return f"{name} isn't valid"


# Paths


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


def _name(path: str) -> str:
    return f"`{path}`" if path else "The spec"


# The expected type at an error location


def _strip(annotation: Any) -> Any:
    """Without Annotated wrappers and without None in an optional."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (Union, types.UnionType):
        options = [a for a in get_args(annotation) if a is not type(None)]
        if len(options) == 1:
            return _strip(options[0])
    return annotation


def _options(annotation: Any) -> list[Any]:
    if get_origin(annotation) in (Union, types.UnionType):
        return [_strip(a) for a in get_args(annotation)]
    return []


def _annotation_at(root: Any, loc: Sequence[str | int]) -> Any:
    """The annotation the spec expects at `loc` (tags included), or None if unknown."""
    current = root
    for part in loc:
        current = _strip(current)
        options = _options(current)
        if options:
            if part in _TAGGED and _TAGGED[part] in options:
                current = _TAGGED[part]
                continue
            return None
        origin = get_origin(current)
        if isinstance(part, int):
            if origin in (tuple, list):
                current = get_args(current)[0]
                continue
            return None
        if _is_model(current):
            field = _field(current, part)
            if field is None:
                return None
            current = field.annotation
            continue
        if origin is dict:
            current = get_args(current)[1]
            continue
        return None
    return _strip(current)


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _field(model: type[BaseModel], name: str | int) -> Any:
    for field_name, field in model.model_fields.items():
        if name in (field_name, field.alias):
            return field
    return None


def _fields(model: type[BaseModel]) -> list[str]:
    return [field.alias or name for name, field in model.model_fields.items()]


def _literal_choices(annotation: Any) -> list[str]:
    if get_origin(annotation) is Literal:
        return [str(a) for a in get_args(annotation)]
    return []


def _list_noun(root: Any, loc: Sequence[str | int]) -> str:
    owner = _annotation_at(root, loc[:-1])
    kind = (
        ""
        if owner in (DashboardSpec, None) and len(loc) == 1
        else "block"
        if owner is Block
        else "filter"
        if owner in (DimensionFilter, TimeFilter)
        else "interaction"
        if owner is Interaction
        else "?"
    )
    return _LIST_NOUNS.get((kind, str(loc[-1])), "items")


def _quoted(text: str) -> list[str]:
    """'day', 'week' or 'year' -> [day, week, year]; 0 -> [0]."""
    found = re.findall(r"'([^']*)'", text)
    return found or ([text] if text else [])


def _ticks(names: Iterable[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)
