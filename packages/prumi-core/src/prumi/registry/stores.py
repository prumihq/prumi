"""Where the registry keeps its tables: a SQLite file, or a Postgres database.

The registry's SQL is written once, in the dialect both accept, with `?` placeholders. A store
opens connections and transactions, and lists the migrations to run. A migration whose SQL
can't be the same on Postgres has a file of the same name under `migrations/postgres/`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import psycopg

    from prumi.config import RegistryPostgresConfig

# Every writer on a Postgres registry takes this transaction-level advisory lock, so writes are
# serialized the way SQLite's BEGIN IMMEDIATE serializes them. Any fixed 64-bit number works;
# this one spells "prumi" in ASCII.
_POSTGRES_LOCK_KEY = 0x7072756D69


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

    def has_table(self, conn: Conn, name: str) -> bool: ...

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
        import psycopg

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
        return psycopg.connect(**options, autocommit=True, row_factory=_tuple_rows)

    @contextmanager
    def connect(self) -> Iterator[_PostgresConn]:
        # One connection per operation, like the SQLite store: safe across threads.
        with self._open() as conn:
            yield _PostgresConn(conn)

    @contextmanager
    def transaction(self) -> Iterator[_PostgresConn]:
        with self._open() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_POSTGRES_LOCK_KEY,))
            yield _PostgresConn(conn)

    def migration_files(self) -> list[tuple[int, Traversable]]:
        migrations = _migrations(_MIGRATIONS)
        migrations.update(_migrations(_MIGRATIONS.joinpath("postgres")))
        return _numbered(migrations)

    def has_table(self, conn: Conn, name: str) -> bool:
        [found] = conn.execute("SELECT to_regclass(?) IS NOT NULL", (name,)).fetchone()
        return found

    def before_first_migration(self, conn: Conn) -> None:
        pass


class _PostgresConn:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> psycopg.Cursor:
        if not params:  # psycopg reads `%` as a placeholder only when given parameters
            return self._conn.execute(sql)
        return self._conn.execute(sql.replace("%", "%%").replace("?", "%s"), tuple(params))


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
