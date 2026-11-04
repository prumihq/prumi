"""Postgres: a read-only session for each query, as a fixed role.

prumi.toml never holds the password: `password_env` names the environment variable that does.
Prumi reads it once at startup and hands it to the driver only, so it can't reach a log, an
error message or anything Prumi shows.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prumi.adapters import AdapterStartupError, IdentityMode, Viewer

from prumi_adapter_dbt.connections import ConnectionFailed, refuse_unknown_keys

try:
    import psycopg
    from psycopg import errors
except ImportError as exc:  # the `postgres` extra isn't installed
    raise AdapterStartupError(
        "The postgres connection needs the psycopg package: install prumi-adapter-dbt[postgres]."
    ) from exc

_KEYS = {"type", "host", "port", "dbname", "user", "password_env", "sslmode", "timezone"}
_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
# At most five digits: int() refuses very long digit strings with its own error.
_PORT = re.compile(r"[0-9]{1,5}")
_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Only a name in the usual upper-case form is shown in messages: anything else may be the
# password itself, typed into `password_env` by mistake.
_SHOWN_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
_PASSWORD_HINT = "the password in the variable `password_env` names"
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


def _refuse_connection_string(config: Mapping[str, Any], key: str, what: str) -> None:
    value = config[key]
    if "://" in value or "=" in value or "@" in value or any(c.isspace() for c in value):
        # A URI or `key=value` string pasted here can hold a password, and messages name this
        # setting's value, so it's refused before anything can echo it.
        raise _bad(
            key,
            f"{what}, not a connection string; the password goes in the variable "
            "`password_env` names",
        )


def _port(config: Mapping[str, Any]) -> int:
    # Settings from environment variables arrive as text, so "5433" counts as 5433.
    port = config.get("port", 5432)
    if isinstance(port, str) and _PORT.fullmatch(port):
        port = int(port)
    if not isinstance(port, int) or isinstance(port, bool):
        raise _bad("port", "an integer")
    if not 1 <= port <= 65535:
        raise _bad("port", "between 1 and 65535")
    return port


def _password(
    config: Mapping[str, Any], environ: Mapping[str, str]
) -> tuple[str | None, str | None]:
    """The variable `password_env` names, and the password it holds; (None, None) without one."""
    name = config.get("password_env")
    if name is None:
        return None, None
    if not isinstance(name, str) or not _VARIABLE_NAME.fullmatch(name):
        raise _bad(
            "password_env", "the name of an environment variable, such as WAREHOUSE_PASSWORD"
        )
    password = environ.get(name, "")
    if not password.strip():
        variable = (
            f"`password_env` names the variable {name}, which is"
            if _SHOWN_NAME.fullmatch(name)
            else "the variable `password_env` names is"
        )
        raise AdapterStartupError(
            f"[source.dbt.connection] {variable} unset or empty: set it to the Postgres password."
        )
    return name, password


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
    password_env: str | None = None
    # Never in repr, so no message or log that shows the settings can show the password.
    _password: str | None = field(default=None, repr=False)

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any], environ: Mapping[str, str] | None = None
    ) -> PostgresSettings:
        """The settings from `[source.dbt.connection]`, with the password read from `environ`
        (the process's environment by default)."""
        if "password" in config:
            raise AdapterStartupError(
                "[source.dbt.connection] takes no `password`: prumi.toml never holds one. "
                "Put the password in an environment variable and name that variable in "
                "`password_env`."
            )
        refuse_unknown_keys(config, _KEYS, "postgres")
        host = _required_str(config, "host", "the Postgres server")
        dbname = _required_str(config, "dbname", "the database dbt built")
        user = _required_str(config, "user", "the Postgres role Prumi connects as")
        # `user` may hold an @ (hosted servers use logins like reader@server), so only these two.
        _refuse_connection_string(config, "host", "a host name")
        _refuse_connection_string(config, "dbname", "a database name")
        port = _port(config)
        password_env, password = _password(config, os.environ if environ is None else environ)
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
        return cls(host, port, dbname, user, sslmode, timezone, password_env, password)

    def connect_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for psycopg.connect, with the password when `password_env` names
        one. Without it libpq looks for a password on its own, as it always does."""
        # Read-only is set for the whole session, so no statement Prumi runs can write. DateStyle
        # only shapes how dates print, and psycopg reads timestamps only in ISO format, whatever
        # the role or database sets.
        options = ["-c default_transaction_read_only=on", "-c DateStyle=ISO"]
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
        if self._password is not None:
            kwargs["password"] = self._password
        return kwargs


_AUTH_FAILED = ("28P01", "28000")  # invalid_password, invalid_authorization_specification

# psycopg gives no SQLSTATE for a failed connection attempt, only libpq's text. These server
# messages stand in for their codes. They classify the failure and are never shown: a message
# no pattern knows (say, a server set to another language) gets the general advice.
_SERVER_MESSAGES = (
    (re.compile(r"password authentication failed"), "28P01"),
    # A pg_hba.conf rejection matches no pattern on purpose: it's about where the connection
    # comes from or its encryption, not the login, so it gets the general advice.
    (re.compile(r"authentication failed"), "28000"),
    (re.compile(r"role \".*\" does not exist"), "28000"),
    (re.compile(r"database \".*\" does not exist"), "3D000"),
    (re.compile(r"invalid value for parameter \"timezone\"", re.IGNORECASE), "22023"),
)


def _sqlstate(exc: psycopg.Error) -> str | None:
    if exc.sqlstate:
        return exc.sqlstate
    if exc.pgconn is not None and exc.pgconn.needs_password:
        # The server asked for a password and libpq found none.
        return "28P01"
    text = str(exc)
    return next((code for pattern, code in _SERVER_MESSAGES if pattern.search(text)), None)


def connect_error_message(exc: psycopg.Error, settings: PostgresSettings) -> str:
    """Why `settings` couldn't connect, in Prumi's own words.

    Built from the non-secret settings only, never from the driver's message or a connection
    string: either could carry the password.
    """
    where = f"{settings.host}:{settings.port}"
    code = _sqlstate(exc)
    if code in _AUTH_FAILED:
        return (
            f"Postgres refused {settings.user}'s login to {settings.dbname} on {where}: check "
            f"`user`, and {_PASSWORD_HINT} (Prumi never reads one from prumi.toml)."
        )
    if code == "3D000":
        return f"No database {settings.dbname} on {where}: check `dbname`."
    if code == "22023" and settings.timezone is not None:
        return (
            f"`timezone` under [source.dbt.connection]: {settings.timezone!r} isn't a time "
            "zone Postgres knows."
        )
    return (
        f"Prumi can't connect to Postgres at {where} as {settings.user}: check `host`, `port` "
        f"and `sslmode`, that the server accepts connections from this machine, and "
        f"{_PASSWORD_HINT}."
    )


# "db"."schema"."table", as dbt writes a relation into the manifest ("" escapes a quote).
_RELATION = re.compile(r'"((?:[^"]|"")*)"\.("(?:[^"]|"")*")\.("(?:[^"]|"")*")')


class PostgresConnection:
    # One role for every viewer: Postgres applies that role's grants and row policies.
    identity_mode: IdentityMode = "shared_account"
    missing_hint = (
        "On Postgres the schema is the one in the dbt profile's target when the project was parsed."
    )

    def __init__(self, settings: PostgresSettings) -> None:
        self.settings = settings
        self.description = (
            f"the Postgres database {settings.dbname} on {settings.host}:{settings.port}"
        )

    @classmethod
    def from_config(cls, config: Mapping[str, Any], config_dir: Path) -> PostgresConnection:
        """Connect once, so a wrong host, login, database, `sslmode` or `timezone` stops the
        server at startup. The server checks the time zone when the session starts."""
        connection = cls(PostgresSettings.from_config(config))
        with connection._connect(AdapterStartupError) as conn:
            if connection.settings.timezone is not None:
                connection._check_timezone(conn)
        return connection

    def _check_timezone(self, conn: psycopg.Connection) -> None:
        """Stop if the session's time zone isn't the configured one.

        libpq applies PGTZ after `options`, so it would silently win over `timezone` and move
        every day boundary. The configured zone is set again on this throwaway session to get the
        server's own spelling of it (`utc` reads back as `UTC`) before comparing.
        """
        try:
            session = conn.execute("SHOW TimeZone").fetchone()[0]
            configured = conn.execute(
                "SELECT set_config('TimeZone', %s, false)", (self.settings.timezone,)
            ).fetchone()[0]
        except psycopg.Error as exc:
            # As at query time, only the error's type leaves the adapter.
            message = f"Checking the time zone on {self.description} failed: {type(exc).__name__}."
        else:
            if session == configured:
                return
            message = (
                f"`timezone` under [source.dbt.connection] is {self.settings.timezone!r}, but the "
                "session got another time zone: something overrides it, such as the PGTZ "
                "environment variable. Unset PGTZ so `timezone` applies."
            )
        # Raised outside the except block, so no driver error rides along as the __context__.
        raise AdapterStartupError(message) from None

    def _connect(self, failure: type[Exception]) -> psycopg.Connection:
        try:
            return psycopg.connect(autocommit=True, **self.settings.connect_kwargs())
        except psycopg.Error as exc:
            message = connect_error_message(exc, self.settings)
        # Raised outside the except block, so the driver's error isn't even the __context__:
        # its connection object still holds the password.
        raise failure(message) from None

    def query(self, sql: str, viewer: Viewer) -> tuple[list[str], list[tuple[Any, ...]]]:
        with self._connect(ConnectionFailed) as conn:
            cursor = conn.execute(sql)
            names = [column.name for column in cursor.description]
            return names, cursor.fetchall()

    def missing_relations(self, relations: Iterable[str]) -> list[str]:
        relations = list(relations)
        self._refuse_other_databases(relations)
        missing: list[str] = []
        unreadable: list[str] = []
        with self._connect(AdapterStartupError) as conn:
            for relation in relations:
                try:
                    conn.execute(f"SELECT 1 FROM {relation} LIMIT 0")
                except (errors.UndefinedTable, errors.InvalidSchemaName):
                    missing.append(relation)
                except errors.InsufficientPrivilege:
                    # No SELECT on the table, or no USAGE on its schema.
                    unreadable.append(relation)
                except psycopg.Error as exc:
                    # As at query time, only the error's type leaves the adapter.
                    raise AdapterStartupError(
                        f"Checking {relation} on {self.description} failed: {type(exc).__name__}."
                    ) from None
        if unreadable:
            user = self.settings.user
            raise AdapterStartupError(
                f"{user} can't read these tables from the semantic manifest on "
                f"{self.description}: {', '.join(unreadable)}. Grant SELECT on these tables to "
                f"{user}, and USAGE on their schemas."
            )
        return missing

    def _refuse_other_databases(self, relations: list[str]) -> None:
        dbname = self.settings.dbname
        databases = set()
        for relation in relations:
            match = _RELATION.fullmatch(relation)
            if match:
                databases.add(match.group(1).replace('""', '"'))
        others = sorted(databases - {dbname})
        if others:
            raise AdapterStartupError(
                f"The semantic manifest was parsed against database {', '.join(others)}, but "
                f"the connection's `dbname` is {dbname}, and Postgres can't query across "
                "databases. Set `dbname` under [source.dbt.connection] to the database dbt "
                "built, or run `dbt parse` against this one."
            )
