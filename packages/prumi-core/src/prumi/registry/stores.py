"""Where the registry keeps its tables: a SQLite file, or a Postgres database.

The registry's SQL is written once, in the dialect both accept, with `?` placeholders. On
Postgres every `?` in a statement run with parameters becomes a placeholder, so such a
statement never holds a `?` anywhere else (in a literal or a comment). A store opens
connections and transactions, and lists the migrations to run. A migration whose SQL can't be
the same on Postgres has a file of the same name under `migrations/postgres/`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from prumi.registry import postgres_errors

if TYPE_CHECKING:
    import psycopg

    from prumi.config import RegistryPostgresConfig

# Every writer on a Postgres registry takes this transaction-level advisory lock, so writes are
# serialized the way SQLite's BEGIN IMMEDIATE serializes them. Any fixed 64-bit number works;
# this one spells "prumi" in ASCII.
_POSTGRES_LOCK_KEY = 0x7072756D69


class RegistryError(Exception):
    """The registry can't be used. The message names what to change, and never a password."""


class Rows(Protocol):
    rowcount: int

    def fetchone(self) -> Any: ...

    def fetchall(self) -> list[Any]: ...


class Conn(Protocol):
    """A connection. Each row reads as `row["col"]`, as `dict(row)` and as a tuple."""

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Rows: ...


class Store(Protocol):
    name: str

    def connect(self) -> AbstractContextManager[Conn]:
        """A connection for one operation; each statement commits on its own."""
        ...

    def transaction(self) -> AbstractContextManager[Conn]:
        """A connection inside a transaction that writers take one at a time; it commits when
        the block ends and rolls back when it raises."""
        ...

    def migration_files(self) -> list[tuple[int, Traversable]]:
        """Each migration's number and file, in order."""
        ...

    def has_table(self, conn: Conn, name: str) -> bool:
        """Whether the registry's database has a table `name` (where the registry's tables go)."""
        ...

    def before_first_migration(self, conn: Conn) -> None:
        """Called in the first migration's transaction when the registry has no tables yet."""
        ...


def _migrations(directory: Traversable) -> dict[str, Traversable]:
    return {f.name: f for f in directory.iterdir() if f.is_file() and f.name.endswith(".sql")}


def _numbered(migrations: dict[str, Traversable]) -> list[tuple[int, Traversable]]:
    return sorted(((int(name.split("_", 1)[0]), f) for name, f in migrations.items()))


_MIGRATIONS = files("prumi.registry").joinpath("migrations")


class SqliteStore:
    """`registry.sqlite3` in a data directory, in WAL mode so the CLI and the server can use it
    at the same time."""

    name = "sqlite"

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "registry.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # One connection per operation: cheap for SQLite, and safe across threads.
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def migration_files(self) -> list[tuple[int, Traversable]]:
        return _numbered(_migrations(_MIGRATIONS))

    def has_table(self, conn: Conn, name: str) -> bool:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return found is not None

    def before_first_migration(self, conn: Conn) -> None:
        pass


class PostgresStore:
    """A Postgres database of the registry's own. psycopg is imported on first use, so core
    without the `postgres` extra still imports."""

    name = "postgres"

    def __init__(self, settings: RegistryPostgresConfig, password: str | None) -> None:
        self.settings = settings
        self._password = password

    def _open(self) -> psycopg.Connection:
        try:
            import psycopg
        except ImportError:
            psycopg = None  # the `postgres` extra isn't installed
        if psycopg is None:  # raised here, after the except block, to leave no __context__
            raise RegistryError(postgres_errors.MISSING_DRIVER_MESSAGE) from None
        options: dict[str, Any] = {
            "host": self.settings.host,
            "port": self.settings.port,
            "dbname": self.settings.dbname,
            "user": self.settings.user,
            "connect_timeout": 10,
            "options": "-c DateStyle=ISO",
        }
        if self.settings.sslmode is not None:
            options["sslmode"] = self.settings.sslmode
        if self._password is not None:
            options["password"] = self._password
        try:
            return psycopg.connect(**options, autocommit=True, row_factory=_tuple_rows)
        except psycopg.Error as exc:
            message = postgres_errors.connect_message(exc, self.settings)
        # Raised outside the except block, so the driver's error isn't even the __context__:
        # its connection object still holds the password.
        raise RegistryError(message) from None

    @contextmanager
    def connect(self) -> Iterator[_PostgresConn]:
        # One connection per operation, like the SQLite store: safe across threads.
        with self._open() as conn:
            yield _PostgresConn(conn, self.settings)

    @contextmanager
    def transaction(self) -> Iterator[_PostgresConn]:
        with self._open() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_POSTGRES_LOCK_KEY,))
            yield _PostgresConn(conn, self.settings)

    def migration_files(self) -> list[tuple[int, Traversable]]:
        migrations = _migrations(_MIGRATIONS)
        migrations.update(_migrations(_MIGRATIONS.joinpath("postgres")))
        return _numbered(migrations)

    def has_table(self, conn: Conn, name: str) -> bool:
        [found] = conn.execute("SELECT to_regclass(?) IS NOT NULL", (name,)).fetchone()
        return found

    def before_first_migration(self, conn: _PostgresConn) -> None:
        """Refuse a database that holds anything else: the registry needs one of its own, so
        it never mixes its tables with a warehouse's or another application's. Every schema
        counts but Postgres's own, and pg_class lists tables this role can't read too.

        Also refuse a database not in UTF8: keys sorted with COLLATE "C" sort by their bytes,
        which matches SQLite's order only when both hold the same UTF-8 text."""
        [encoding] = conn.execute("SHOW server_encoding").fetchone()
        if encoding != "UTF8":
            raise RegistryError(postgres_errors.not_utf8_message(self.settings, encoding))
        rows = conn.execute(
            "SELECT n.nspname, c.relname FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')"
            # pg_catalog, pg_toast and the temporary schemas: `\_` is a literal underscore.
            " AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\\_%'"
            " ORDER BY 1, 2"
        ).fetchall()
        if rows:
            tables = [f"{schema}.{name}" for schema, name in rows]
            raise RegistryError(postgres_errors.not_its_own_database_message(self.settings, tables))
        # What follows creates the registry's tables, so a privilege error now means just that.
        conn.creating_tables = True


class _PostgresConn:
    def __init__(self, conn: psycopg.Connection, settings: RegistryPostgresConfig) -> None:
        self._conn = conn
        self._settings = settings
        self.creating_tables = False

    def execute(self, sql: str, params: Sequence[Any] = ()) -> psycopg.Cursor:
        from psycopg.errors import InsufficientPrivilege

        try:
            if not params:  # psycopg reads `%` as a placeholder only when given parameters
                return self._conn.execute(sql)
            return self._conn.execute(sql.replace("%", "%%").replace("?", "%s"), tuple(params))
        except InsufficientPrivilege:
            if not self.creating_tables:
                raise
        # Raised outside the except block, so no driver error rides along as the __context__.
        raise RegistryError(postgres_errors.cannot_create_tables_message(self._settings)) from None


class _Row(tuple):
    """A row that reads like `sqlite3.Row`: by index, by column name, and as `dict(row)`."""

    _columns: dict[str, int]

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return super().__getitem__(self._columns[key])
        return super().__getitem__(key)

    def keys(self) -> list[str]:
        return list(self._columns)


def _tuple_rows(cursor: psycopg.Cursor) -> Any:
    columns = {column.name: i for i, column in enumerate(cursor.description or ())}

    def make(values: Sequence[Any]) -> _Row:
        row = _Row(values)
        row._columns = columns
        return row

    return make
