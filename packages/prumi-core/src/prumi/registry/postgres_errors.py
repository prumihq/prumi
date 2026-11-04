"""Why a Postgres registry can't be used, in Prumi's own words.

Messages are built from the non-secret settings only, never from the driver's text or a
connection string: either could carry the password, which comes from PRUMI_REGISTRY_PASSWORD.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from prumi.config import REGISTRY_PASSWORD_VARIABLE

if TYPE_CHECKING:
    import psycopg

    from prumi.config import RegistryPostgresConfig

SECTION = "[registry.postgres]"
_PASSWORD_HINT = f"the password in {REGISTRY_PASSWORD_VARIABLE}"
_LOGIN_REFUSED = ("28P01", "28000")  # invalid_password, invalid_authorization_specification
_NO_DATABASE = "3D000"
_NO_CONNECT = "42501"  # insufficient_privilege: here, no CONNECT on the database
_SHOWN_TABLES = 5

# psycopg gives no SQLSTATE when a connection attempt fails, only libpq's text. These server
# messages stand in for their codes; they only pick the advice and are never shown. A message
# none of them knows (a server in another language, say) gets the general advice.
_SERVER_MESSAGES = (
    (re.compile(r"password authentication failed"), "28P01"),
    # A pg_hba.conf rejection is left out on purpose: it's about where the connection comes
    # from or its encryption, not the login, so it gets the general advice.
    (re.compile(r"authentication failed"), "28000"),
    (re.compile(r"role \".*\" does not exist"), "28000"),
    (re.compile(r"database \".*\" does not exist"), _NO_DATABASE),
    (re.compile(r"permission denied for database"), _NO_CONNECT),
)


def _where(settings: RegistryPostgresConfig) -> str:
    return f"{settings.host}:{settings.port}"


def _sqlstate(exc: psycopg.Error) -> str | None:
    if exc.sqlstate:
        return exc.sqlstate
    if exc.pgconn is not None and exc.pgconn.needs_password:
        return "28P01"  # the server asked for a password and none was given
    text = str(exc)
    return next((code for pattern, code in _SERVER_MESSAGES if pattern.search(text)), None)


def connect_message(exc: psycopg.Error, settings: RegistryPostgresConfig) -> str:
    """Why connecting to the registry's database failed, and which setting to check."""
    where, code = _where(settings), _sqlstate(exc)
    if code in _LOGIN_REFUSED:
        return (
            f"Postgres refused {settings.user}'s login to the registry database "
            f"{settings.dbname} on {where}: check `user` under {SECTION}, and {_PASSWORD_HINT}."
        )
    if code == _NO_DATABASE:
        return (
            f"No database {settings.dbname} on {where} for the registry: create it, or check "
            f"`dbname` under {SECTION}."
        )
    if code == _NO_CONNECT:
        return (
            f"{settings.user} may not connect to the registry database {settings.dbname} on "
            f"{where}: grant it (`GRANT CONNECT ON DATABASE {settings.dbname} TO "
            f"{settings.user}`), or check `user` and `dbname` under {SECTION}."
        )
    return (
        f"Prumi can't connect to the registry database {settings.dbname} on {where} as "
        f"{settings.user}: check `host`, `port`, `dbname` and `sslmode` under {SECTION}, that "
        f"the server accepts connections from this machine, and {_PASSWORD_HINT}."
    )


def not_its_own_database_message(settings: RegistryPostgresConfig, tables: Sequence[str]) -> str:
    """The database already holds `tables`, so it isn't the registry's alone."""
    shown = ", ".join(tables[:_SHOWN_TABLES])
    more = f" and {len(tables) - _SHOWN_TABLES} more" if len(tables) > _SHOWN_TABLES else ""
    return (
        f"The registry needs its own database, but {settings.dbname} on {_where(settings)} "
        f"already holds other tables ({shown}{more}). Create an empty database for Prumi's "
        f"registry and set `dbname` under {SECTION} to it."
    )


def not_utf8_message(settings: RegistryPostgresConfig, encoding: str) -> str:
    return (
        f"The registry database {settings.dbname} on {_where(settings)} is in {encoding}, and "
        "the registry needs UTF8 encoding. Create a database for it in UTF8 (`CREATE DATABASE "
        f"<name> ENCODING 'UTF8' TEMPLATE template0`) and set `dbname` under {SECTION} to it."
    )


def busy_message(settings: RegistryPostgresConfig) -> str:
    return (
        f"Another writer holds the registry database {settings.dbname} on {_where(settings)} "
        "and didn't let go within 10 seconds: try again, and if it keeps happening, look for "
        "a stuck Prumi process or a long transaction on that database."
    )


def cannot_create_tables_message(settings: RegistryPostgresConfig) -> str:
    return (
        f"{settings.user} can't create tables in the registry database {settings.dbname} on "
        f"{_where(settings)}. Make {settings.user} the database's owner, or grant it CREATE on "
        f"the schema the registry uses (public by default); or set `user` under {SECTION} to "
        "a role that can."
    )


MISSING_DRIVER_MESSAGE = (
    "The Postgres registry needs the psycopg package: install prumi[postgres], or set "
    '`backend = "sqlite"` under [registry].'
)
