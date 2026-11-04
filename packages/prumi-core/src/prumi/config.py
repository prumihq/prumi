"""prumi.toml and PRUMI__ environment variables: the server, the allowed CDN hosts, the source."""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from prumi.container import in_container

DEFAULT_SOURCE = "default"
DEFAULT_CONFIG = "prumi.toml"
ENVIRONMENT = "the environment"
DEFAULT_LOCALE = "en-US"
# A BCP 47 tag's shape. Whether the browser knows it is checked when the server starts.
_LOCALE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")

DEFAULT_SCRIPT_HOSTS = ("cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com")
DEFAULT_STYLE_HOSTS = (
    "fonts.googleapis.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "unpkg.com",
)
DEFAULT_FONT_HOSTS = ("fonts.gstatic.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com")


class ConfigError(Exception):
    """The settings can't be used. The message names the file or variable and what to change."""


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    data_dir: Path
    as_of: dt.date | None
    locale: str = DEFAULT_LOCALE
    script_hosts: tuple[str, ...] = DEFAULT_SCRIPT_HOSTS
    style_hosts: tuple[str, ...] = DEFAULT_STYLE_HOSTS
    font_hosts: tuple[str, ...] = DEFAULT_FONT_HOSTS


@dataclass(frozen=True)
class SourceConfig:
    name: str
    adapter: str
    settings: Mapping[str, Any]  # the adapter's own table, `[source.<adapter>]`
    config_dir: Path  # relative paths in `settings` resolve against it
    project: str | None = None  # a display name for people (home page); adapters never see it


@dataclass(frozen=True)
class Config:
    path: Path | None  # None when the settings came from PRUMI__ variables alone
    server: ServerConfig
    sources: Mapping[str, SourceConfig]  # exactly one in this milestone, named "default"
    # `key path -> variable name` for each setting a PRUMI__ variable gave, so a later message
    # can name the variable instead of showing a value that may be a secret.
    set_by: Mapping[tuple[str, ...], str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def source_label(self) -> str:
        """Where the settings came from, for messages: the file, or the environment."""
        return str(self.path) if self.path is not None else ENVIRONMENT


class _Server(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # In the container image the default is every address (docs/adr/0017).
    host: str = Field(default_factory=lambda: "0.0.0.0" if in_container() else "127.0.0.1")
    port: int = Field(default=8484, ge=1, le=65535)
    data_dir: str = ".prumi"
    as_of: dt.date | None = None
    locale: str = DEFAULT_LOCALE
    script_hosts: tuple[str, ...] = DEFAULT_SCRIPT_HOSTS
    style_hosts: tuple[str, ...] = DEFAULT_STYLE_HOSTS
    font_hosts: tuple[str, ...] = DEFAULT_FONT_HOSTS

    @field_validator("script_hosts", "style_hosts", "font_hosts", mode="before")
    @classmethod
    def _comma_separated(cls, hosts: Any) -> Any:
        # A variable holds text, so a list setting comes as comma-separated hosts.
        if isinstance(hosts, str):
            return tuple(host.strip() for host in hosts.split(",") if host.strip())
        return hosts

    @field_validator("locale")
    @classmethod
    def _locale_tag(cls, locale: str) -> str:
        if not _LOCALE.fullmatch(locale):
            raise ValueError(
                f"{locale!r} isn't a locale tag; use a BCP 47 tag such as en-US, pt-BR or de-DE"
            )
        return locale

    @field_validator("host")
    @classmethod
    def _loopback(cls, host: str) -> str:
        if in_container():
            return host
        if not is_loopback(host):
            raise ValueError(
                f"{host!r} isn't a loopback address. Prumi has no login in this release, so it "
                "only listens on 127.0.0.1, ::1 or localhost; anything else would expose the "
                "warehouse to the network"
            )
        return host


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_config(path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> Config:
    """Read prumi.toml and the PRUMI__ variables over it.

    With no `path`, ./prumi.toml is read if it exists; otherwise the settings come from the
    variables alone. A `path` given explicitly must exist.
    """
    # config_env raises this module's ConfigError, so it's imported here, not at the top.
    from prumi.config_env import PREFIX, from_environment, merge

    settings = from_environment(os.environ if environ is None else environ)
    explicit = path is not None
    file = Path(DEFAULT_CONFIG if path is None else path).resolve()
    try:
        with file.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f"The config file {file} doesn't exist.") from None
        if not settings:
            raise ConfigError(
                f"There's no config file at {file} and no {PREFIX} variable is set. Write a "
                f"prumi.toml, pass --config, or give the settings as {PREFIX} variables (e.g. "
                f"{PREFIX}SOURCE__ADAPTER=dbt)."
            ) from None
        data, found = {}, None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file} isn't valid TOML: {exc}") from None
    else:
        found = file

    variables = merge(data, settings)
    where = str(found) if found is not None else ENVIRONMENT
    # Relative paths resolve against the file's folder, or the current one without a file.
    config_dir = found.parent if found is not None else Path.cwd()
    unknown = sorted(set(data) - {"server", "source"})
    if unknown:
        set_by = _set_by(variables, *((section,) for section in unknown))
        raise ConfigError(f"{where}: unknown section(s) {', '.join(unknown)}{set_by}.")
    try:
        server = _Server.model_validate(data.get("server", {}))
    except ValidationError as exc:
        raise ConfigError(_describe(where, "server", exc, variables)) from None

    return Config(
        path=found,
        server=ServerConfig(
            host=server.host,
            port=server.port,
            data_dir=(config_dir / server.data_dir).resolve(),
            as_of=server.as_of,
            locale=server.locale,
            script_hosts=server.script_hosts,
            style_hosts=server.style_hosts,
            font_hosts=server.font_hosts,
        ),
        sources=MappingProxyType(
            {DEFAULT_SOURCE: _source(where, config_dir, data.get("source"), variables)}
        ),
        set_by=MappingProxyType(variables),
    )


def _source(
    where: str, config_dir: Path, table: Any, variables: Mapping[tuple[str, ...], str]
) -> SourceConfig:
    if not isinstance(table, dict) or not isinstance(table.get("adapter"), str):
        # Only a variable that replaced the whole section can be at fault here.
        named = f" (set by {variables[('source',)]})" if ("source",) in variables else ""
        raise ConfigError(
            f'{where}: [source]{named} needs `adapter`, the adapter to use (e.g. "dbt").'
        )
    adapter = table["adapter"]
    unknown = sorted(set(table) - {"adapter", "project", adapter})
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) in [source]: {', '.join(unknown)}"
            f"{_set_by(variables, *(('source', key) for key in unknown))}. The adapter's "
            f"settings go in [source.{adapter}]."
        )
    settings = table.get(adapter, {})
    if not isinstance(settings, dict):
        raise ConfigError(
            f"{where}: [source.{adapter}] must be a table{_set_by(variables, ('source', adapter))}."
        )
    project = table.get("project")
    if project is not None and not isinstance(project, str):
        raise ConfigError(f"{where}: [source] `project` must be a string (the project's name).")
    return SourceConfig(
        name=DEFAULT_SOURCE,
        adapter=adapter,
        settings=settings,
        config_dir=config_dir,
        project=project or None,
    )


def _set_by(variables: Mapping[tuple[str, ...], str], *sections: tuple[str, ...]) -> str:
    """` (set by NAME, ...)` for the variables that set these keys or keys under them."""
    named = sorted(
        name
        for key, name in variables.items()
        if any(key[: len(section)] == section for section in sections)
    )
    return f" (set by {', '.join(named)})" if named else ""


def _describe(
    where: str,
    section: str,
    exc: ValidationError,
    variables: Mapping[tuple[str, ...], str],
) -> str:
    """The validation problems, each under its key, or under the variable that set it."""
    problems = []
    for error in exc.errors():
        path = (section, *(str(part) for part in error["loc"]))
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            message = "unknown key"
        # A list item's location ends in its index; the variable set the whole list.
        name = next(
            (variables[path[:n]] for n in range(len(path), 0, -1) if path[:n] in variables), None
        )
        if name is None:
            problems.append(f"{'.'.join(path)}: {message}")
            continue
        # A variable may hold a secret, so its value never appears in a message.
        value = error.get("input")
        if isinstance(value, str) and value:
            message = message.replace(repr(value), "the value")
        problems.append(f"{name}: {message}")
    return f"{where}: " + "; ".join(problems)
