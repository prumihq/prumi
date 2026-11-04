"""Settings from PRUMI__ environment variables, merged over prumi.toml's table.

A variable is `PRUMI__` plus the key's path, upper case, joined by double underscores:
`[source.dbt.connection] host` is `PRUMI__SOURCE__DBT__CONNECTION__HOST`. Single-underscore
names (`PRUMI_CONTAINER`, `PRUMI_REGISTRY_PASSWORD`, ...) are not settings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from prumi.config import ConfigError

PREFIX = "PRUMI__"
# One part of a name: upper-case words joined by single underscores (SEMANTIC_MANIFEST).
_PART = re.compile(r"[A-Z0-9]+(?:_[A-Z0-9]+)*")


def from_environment(environ: Mapping[str, str]) -> dict[tuple[str, ...], tuple[str, str]]:
    """Each set PRUMI__ variable as `key path -> (variable name, value)`.

    An empty variable counts as unset, so a compose file can pass an optional setting
    through as `${X:-}`.
    """
    settings: dict[tuple[str, ...], tuple[str, str]] = {}
    for name, value in environ.items():
        if not name.startswith(PREFIX) or value == "":
            continue
        parts = name.removeprefix(PREFIX).split("__")
        if not all(_PART.fullmatch(part) for part in parts):
            raise ConfigError(
                f"{name} isn't a settings variable name: after {PREFIX}, use the key's path in "
                "upper case, with its parts joined by double underscores (e.g. "
                "PRUMI__SOURCE__DBT__CONNECTION__HOST)."
            )
        settings[tuple(part.lower() for part in parts)] = (name, value)
    return settings


def merge(
    table: dict[str, Any], settings: Mapping[tuple[str, ...], tuple[str, str]]
) -> dict[tuple[str, ...], str]:
    """Set each value into `table`, creating sections; return `key path -> variable name`.

    The environment wins over the file. A variable can't replace a section, nor set a key
    under a value that isn't a section.
    """
    variables: dict[tuple[str, ...], str] = {}
    for path, (name, value) in sorted(settings.items()):
        section = table
        for depth, part in enumerate(path[:-1]):
            child = section.setdefault(part, {})
            if not isinstance(child, dict):
                where = ".".join(path[: depth + 1])
                raise ConfigError(f"{name}: {where} is a value, not a section.")
            section = child
        if isinstance(section.get(path[-1]), dict):
            raise ConfigError(f"{name}: {'.'.join(path)} is a section, not a value.")
        section[path[-1]] = value
        variables[path] = name
    return variables
