"""Postgres: connection settings, and (with them) a read-only session for each query.

The password never passes through Prumi: libpq finds it in PGPASSWORD, ~/.pgpass or the service
file, so it can't reach a config file, a log or an error message.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from prumi.adapters import AdapterStartupError

from prumi_adapter_dbt.connections import refuse_unknown_keys

try:
    import psycopg  # noqa: F401
except ImportError as exc:  # the `postgres` extra isn't installed
    raise AdapterStartupError(
        "The postgres connection needs the psycopg package: install prumi-adapter-dbt[postgres]."
    ) from exc

_KEYS = {"type", "host", "port", "dbname", "user", "sslmode", "timezone"}
_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
CONNECT_TIMEOUT_SECONDS = 10


def _bad(key: str, wanted: str) -> AdapterStartupError:
    # Never the value: it could be a password in the wrong key.
    return AdapterStartupError(f"[source.dbt.connection] `{key}` must be {wanted}.")


def _required_str(config: Mapping[str, Any], key: str, what: str) -> str:
    value = config.get(key)
    if key not in config:
        raise AdapterStartupError(f"[source.dbt.connection] needs `{key}`, {what}.")
    if not isinstance(value, str) or not value:
        raise _bad(key, "a non-empty string")
    return value


def _escape_option(value: str) -> str:
    """libpq's `options` is split on spaces; a backslash escapes a space or a backslash."""
    return value.replace("\\", "\\\\").replace(" ", "\\ ")


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    dbname: str
    user: str
    sslmode: str | None
    timezone: str | None

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> PostgresSettings:
        if "password" in config:
            raise AdapterStartupError(
                "[source.dbt.connection] takes no `password`: prumi.toml never holds one. "
                "Set PGPASSWORD, or put it in ~/.pgpass (or a libpq service file); libpq finds it."
            )
        refuse_unknown_keys(config, _KEYS, "postgres")
        host = _required_str(config, "host", "the Postgres server")
        dbname = _required_str(config, "dbname", "the database dbt built")
        user = _required_str(config, "user", "the Postgres role Prumi connects as")
        port = config.get("port", 5432)
        if not isinstance(port, int) or isinstance(port, bool):
            raise _bad("port", "an integer")
        sslmode = config.get("sslmode")
        if sslmode is not None and (not isinstance(sslmode, str) or sslmode not in _SSLMODES):
            raise _bad("sslmode", f"one of {', '.join(_SSLMODES)}")
        timezone = config.get("timezone")
        if timezone is not None and (not isinstance(timezone, str) or not timezone):
            raise _bad("timezone", "a non-empty string")
        if timezone is not None and any(
            (c.isspace() and c != " ") or not c.isprintable() for c in timezone
        ):
            # The server splits `options` on any whitespace, so a tab or newline could smuggle in
            # another `-c` (such as one turning read-only off). Zone names have neither.
            raise _bad("timezone", "a time zone name without tabs, newlines or control characters")
        return cls(host, port, dbname, user, sslmode, timezone)

    def connect_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for psycopg.connect; never a password."""
        # Read-only is set for the whole session, so no statement Prumi runs can write.
        options = ["-c default_transaction_read_only=on"]
        if self.timezone is not None:
            options.append(f"-c TimeZone={_escape_option(self.timezone)}")
        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "options": " ".join(options),
        }
        if self.sslmode is not None:
            kwargs["sslmode"] = self.sslmode
        return kwargs
