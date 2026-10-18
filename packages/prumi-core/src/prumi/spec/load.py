"""Reading specs: JSON from MCP tools, YAML or JSON files from the CLI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


class _SpecLoader(yaml.SafeLoader):
    """YAML 1.1 reads `on`, `off`, `yes` and `no` as booleans, which would turn the
    interaction key `on: click` into `True: click`. Only true/false are booleans here."""


_SpecLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_SpecLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


class SpecFileError(Exception):
    pass


def parse_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_SpecLoader)  # a SafeLoader subclass: no arbitrary objects


def load_spec_file(path: str | Path) -> Any:
    """The raw spec data from a `.yaml`, `.yml` or `.json` file (not validated yet)."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecFileError(f"Can't read {path}: {exc.strerror}") from None
    kind = "JSON" if path.suffix.lower() == ".json" else "YAML"
    try:
        return json.loads(text) if kind == "JSON" else parse_yaml(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SpecFileError(f"{path} isn't valid {kind}: {exc}") from None
