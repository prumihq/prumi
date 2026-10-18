"""prumi.toml (spec section 2)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEFAULT_SOURCE = "default"

DEFAULT_SCRIPT_HOSTS = ("cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com")
DEFAULT_STYLE_HOSTS = (
    "fonts.googleapis.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "unpkg.com",
)
DEFAULT_FONT_HOSTS = ("fonts.gstatic.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com")


class ConfigError(Exception):
    """prumi.toml can't be used. The message names the file and what to change."""


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    data_dir: Path
    as_of: dt.date | None
    script_hosts: tuple[str, ...] = DEFAULT_SCRIPT_HOSTS
    style_hosts: tuple[str, ...] = DEFAULT_STYLE_HOSTS
    font_hosts: tuple[str, ...] = DEFAULT_FONT_HOSTS


@dataclass(frozen=True)
class SourceConfig:
    name: str
    adapter: str
    settings: Mapping[str, Any]  # the adapter's own table, `[source.<adapter>]`
    config_dir: Path  # relative paths in `settings` resolve against it


@dataclass(frozen=True)
class Config:
    path: Path
    server: ServerConfig
    sources: Mapping[str, SourceConfig]  # exactly one in this milestone, named "default"


class _Server(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8484, ge=1, le=65535)
    data_dir: str = ".prumi"
    as_of: dt.date | None = None
    script_hosts: tuple[str, ...] = DEFAULT_SCRIPT_HOSTS
    style_hosts: tuple[str, ...] = DEFAULT_STYLE_HOSTS
    font_hosts: tuple[str, ...] = DEFAULT_FONT_HOSTS

    @field_validator("host")
    @classmethod
    def _loopback(cls, host: str) -> str:
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


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"The config file {path} doesn't exist.") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} isn't valid TOML: {exc}") from None

    config_dir = path.parent
    unknown = sorted(set(data) - {"server", "source"})
    if unknown:
        raise ConfigError(f"{path}: unknown section(s) {', '.join(unknown)}.")
    try:
        server = _Server.model_validate(data.get("server", {}))
    except ValidationError as exc:
        raise ConfigError(_describe(path, "server", exc)) from None

    return Config(
        path=path,
        server=ServerConfig(
            host=server.host,
            port=server.port,
            data_dir=(config_dir / server.data_dir).resolve(),
            as_of=server.as_of,
            script_hosts=server.script_hosts,
            style_hosts=server.style_hosts,
            font_hosts=server.font_hosts,
        ),
        sources=MappingProxyType({DEFAULT_SOURCE: _source(path, data.get("source"))}),
    )


def _source(path: Path, table: Any) -> SourceConfig:
    if not isinstance(table, dict) or not isinstance(table.get("adapter"), str):
        raise ConfigError(f'{path}: [source] needs `adapter`, the adapter to use (e.g. "dbt").')
    adapter = table["adapter"]
    unknown = sorted(set(table) - {"adapter", adapter})
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s) in [source]: {', '.join(unknown)}. The adapter's settings "
            f"go in [source.{adapter}]."
        )
    settings = table.get(adapter, {})
    if not isinstance(settings, dict):
        raise ConfigError(f"{path}: [source.{adapter}] must be a table.")
    return SourceConfig(
        name=DEFAULT_SOURCE, adapter=adapter, settings=settings, config_dir=path.parent
    )


def _describe(path: Path, section: str, exc: ValidationError) -> str:
    problems = []
    for error in exc.errors():
        key = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            message = "unknown key"
        problems.append(f"{section}.{key}: {message}")
    return f"{path}: " + "; ".join(problems)
