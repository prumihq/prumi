"""The registry: dashboards, immutable versions, certifications, metric requests, and the
in-browser number checks.

SQLite in WAL mode, so the CLI and the server can use it at the same time. Status is computed
from certifications on every read, never stored: a version is certified while a non-revoked
certification of the dashboard has its fingerprint.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from prumi.adapters import LOCAL_VIEWER, Viewer
from prumi.issues import Report, error
from prumi.spec import DashboardSpec

Status = Literal["draft", "certified"]
FINGERPRINT_VERSION = "v0"


# Canonical content and fingerprints


def canonical_spec_json(spec: DashboardSpec) -> str:
    """Pydantic dump with defaults, keys sorted, no whitespace."""
    data = spec.model_dump(mode="json", by_alias=True)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize_html(html: str) -> str:
    return html.replace("\r\n", "\n").replace("\r", "\n")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Content:
    spec_json: str
    html: str
    spec_hash: str
    html_hash: str
    fingerprint: str

    @classmethod
    def of(cls, spec: DashboardSpec, html: str) -> Content:
        spec_json, html = canonical_spec_json(spec), normalize_html(html)
        spec_hash, html_hash = _sha256(spec_json), _sha256(html)
        fingerprint = f"{FINGERPRINT_VERSION}:{_sha256(f'{spec_hash}:{html_hash}')}"
        return cls(spec_json, html, spec_hash, html_hash, fingerprint)


# Rows


@dataclass(frozen=True)
class Dashboard:
    id: str
    title: str
    owner: str
    created_at: str
    current_version: int


@dataclass(frozen=True)
class Version:
    dashboard_id: str
    version: int
    spec_json: str
    html: str
    spec_hash: str
    html_hash: str
    fingerprint: str
    created_at: str

    @property
    def spec(self) -> DashboardSpec:
        return DashboardSpec.model_validate_json(self.spec_json)


@dataclass(frozen=True)
class VersionStatus:
    status: Status
    version: int
    current_version: int
    certified_by: str | None
    certified_at: str | None
    fingerprint: str
    # A viewer's browser flagged numbers on the current version: shown after publishing, for
    # new data or a filter state the server didn't check. Cleared by a new version, or by a
    # clean report for the same filter state (reports are kept per filter state).
    needs_attention: bool = False

    def to_json(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class MetricRequest:
    id: int
    description: str
    reason: str
    dashboard_id: str | None
    requested_by: str
    created_at: str


@dataclass(frozen=True)
class PageCheck:
    """What a viewer's browser found on one version, for one filter state."""

    filters: dict[str, Any]
    flagged: list[dict[str, str]]  # [{text, path}]: numbers Prumi didn't deliver
    canvas: bool
    errors: list[str]
    reported_at: str = ""

    @property
    def clean(self) -> bool:
        return not (self.flagged or self.canvas or self.errors)

    def to_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Published:
    dashboard_id: str
    version: int
    created: bool  # False when the content equals the current version's


class Registry:
    def __init__(
        self,
        data_dir: Path,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self.path = Path(data_dir) / "registry.sqlite3"
        self._now = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            _migrate(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # One connection per operation: cheap for SQLite, and safe across threads.
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def now(self) -> dt.datetime:
        """The registry's clock, which stamps every version and request."""
        return self._now()

    def _timestamp(self) -> str:
        return self.now().astimezone(dt.UTC).isoformat(timespec="seconds")

    # Permissions. Every read of a dashboard (tools, block API) asks can_read, and a new
    # version of an existing dashboard asks can_edit. With one local user and no login,
    # everyone may do both; owners, editors and viewers come with login.

    def can_read(self, viewer: Viewer, dashboard: Dashboard) -> bool:
        return True

    def can_edit(self, viewer: Viewer, dashboard: Dashboard) -> bool:
        return True

    # Dashboards and versions

    def save(self, spec: DashboardSpec, html: str, owner: str = "local") -> Published:
        """Store a version (already validated). Identical content creates nothing."""
        content = Content.of(spec, html)
        dashboard_id = spec.dashboard.id
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT current_version FROM dashboards WHERE id = ?", (dashboard_id,)
            ).fetchone()
            now = self._timestamp()
            if row is None:
                version = 1
                conn.execute(
                    "INSERT INTO dashboards (id, title, owner, created_at, current_version)"
                    " VALUES (?, ?, ?, ?, 1)",
                    (dashboard_id, spec.dashboard.title, owner, now),
                )
            else:
                current = row["current_version"]
                [fingerprint] = conn.execute(
                    "SELECT fingerprint FROM versions WHERE dashboard_id = ? AND version = ?",
                    (dashboard_id, current),
                ).fetchone()
                if fingerprint == content.fingerprint:
                    return Published(dashboard_id, current, created=False)
                version = current + 1
                conn.execute(
                    "UPDATE dashboards SET current_version = ?, title = ? WHERE id = ?",
                    (version, spec.dashboard.title, dashboard_id),
                )
            conn.execute(
                "INSERT INTO versions (dashboard_id, version, spec_json, html, spec_hash,"
                " html_hash, fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dashboard_id,
                    version,
                    content.spec_json,
                    content.html,
                    content.spec_hash,
                    content.html_hash,
                    content.fingerprint,
                    now,
                ),
            )
        return Published(dashboard_id, version, created=True)

    def dashboards(self) -> list[Dashboard]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM dashboards ORDER BY id").fetchall()
        return [Dashboard(**dict(r)) for r in rows]

    def dashboard(self, dashboard_id: str) -> Dashboard | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM dashboards WHERE id = ?", (dashboard_id,)).fetchone()
        return Dashboard(**dict(row)) if row else None

    def version(self, dashboard_id: str, version: int) -> Version | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM versions WHERE dashboard_id = ? AND version = ?",
                (dashboard_id, version),
            ).fetchone()
        return Version(**dict(row)) if row else None

    def status(self, dashboard_id: str, version: int) -> VersionStatus | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT v.version, v.fingerprint, d.current_version,
                       c.certified_by, c.certified_at,
                       v.version = d.current_version AND EXISTS (
                           SELECT 1 FROM page_checks p
                           WHERE p.dashboard_id = v.dashboard_id AND p.version = v.version
                             AND p.flagged_json != '[]'
                       ) AS needs_attention
                FROM versions v
                JOIN dashboards d ON d.id = v.dashboard_id
                LEFT JOIN certifications c
                  ON c.id = (SELECT id FROM certifications
                             WHERE dashboard_id = v.dashboard_id
                               AND fingerprint = v.fingerprint AND revoked_at IS NULL
                             ORDER BY certified_at DESC, id DESC LIMIT 1)
                WHERE v.dashboard_id = ? AND v.version = ?
                """,
                (dashboard_id, version),
            ).fetchone()
        if row is None:
            return None
        return VersionStatus(
            status="certified" if row["certified_by"] is not None else "draft",
            version=row["version"],
            current_version=row["current_version"],
            certified_by=row["certified_by"],
            certified_at=row["certified_at"],
            fingerprint=row["fingerprint"],
            needs_attention=bool(row["needs_attention"]),
        )

    # Certification

    def certify(self, dashboard_id: str, version: int, by: str) -> VersionStatus:
        found = self.version(dashboard_id, version)
        if found is None:
            raise KeyError(f"{dashboard_id} version {version}")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO certifications (dashboard_id, version, fingerprint, certified_by,"
                " certified_at) VALUES (?, ?, ?, ?, ?)",
                (dashboard_id, version, found.fingerprint, by, self._timestamp()),
            )
        return self.status(dashboard_id, version)

    def uncertify(self, dashboard_id: str, reason: str | None = None) -> int:
        """Revoke the dashboard's active certifications; returns how many."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE certifications SET revoked_at = ?, revoked_reason = ?"
                " WHERE dashboard_id = ? AND revoked_at IS NULL",
                (self._timestamp(), reason, dashboard_id),
            )
            return cursor.rowcount

    def delete(self, dashboard_id: str) -> bool:
        """Remove the dashboard, its versions and certifications; keep its metric requests."""
        with self._transaction() as conn:
            conn.execute("DELETE FROM page_checks WHERE dashboard_id = ?", (dashboard_id,))
            conn.execute("DELETE FROM certifications WHERE dashboard_id = ?", (dashboard_id,))
            conn.execute("DELETE FROM versions WHERE dashboard_id = ?", (dashboard_id,))
            deleted = conn.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,)).rowcount
            conn.execute(
                "UPDATE metric_requests SET dashboard_id = NULL WHERE dashboard_id = ?",
                (dashboard_id,),
            )
        return bool(deleted)

    # In-browser checks

    def record_check(self, dashboard_id: str, version: int, check: PageCheck) -> None:
        """Keep the latest report for the version and its filter state."""
        filters = json.dumps(check.filters, sort_keys=True, separators=(",", ":"))
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO page_checks (dashboard_id, version, filters_json,"
                " flagged_json, canvas, errors_json, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    dashboard_id,
                    version,
                    filters,
                    json.dumps(check.flagged),
                    int(check.canvas),
                    json.dumps(check.errors),
                    self._timestamp(),
                ),
            )

    def checks(self, dashboard_id: str, version: int) -> list[PageCheck]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM page_checks WHERE dashboard_id = ? AND version = ?"
                " ORDER BY reported_at, filters_json",
                (dashboard_id, version),
            ).fetchall()
        return [
            PageCheck(
                filters=json.loads(r["filters_json"]),
                flagged=json.loads(r["flagged_json"]),
                canvas=bool(r["canvas"]),
                errors=json.loads(r["errors_json"]),
                reported_at=r["reported_at"],
            )
            for r in rows
        ]

    # Metric requests

    def request_metric(
        self,
        description: str,
        reason: str,
        dashboard_id: str | None = None,
        requested_by: str = "local",
    ) -> MetricRequest:
        with self._transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO metric_requests (description, reason, dashboard_id, requested_by,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (description, reason, dashboard_id, requested_by, self._timestamp()),
            )
            request_id = cursor.lastrowid
        return next(r for r in self.metric_requests() if r.id == request_id)

    def metric_requests(self) -> list[MetricRequest]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM metric_requests ORDER BY id").fetchall()
        return [MetricRequest(**dict(r)) for r in rows]

    def metric_request_ids(self) -> set[int]:
        return {r.id for r in self.metric_requests()}


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    [current] = conn.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()
    migrations = sorted(
        (int(f.name.split("_", 1)[0]), f)
        for f in files("prumi.registry").joinpath("migrations").iterdir()
        if f.name.endswith(".sql")
    )
    for number, migration in migrations:
        if number <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Statement by statement: executescript() would commit on its own.
            for statement in _statements(migration.read_text(encoding="utf-8")):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (number,))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def _statements(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


# Publishing


def publish(
    registry: Registry,
    spec: object,
    html: str,
    validate: Callable[[object, str], Report],
    viewer: Viewer = LOCAL_VIEWER,
) -> tuple[Report, Published | None]:
    """Validate, then store. Errors block publishing; the report's warnings are returned."""
    dashboard_id = spec.get("dashboard", {}).get("id") if isinstance(spec, dict) else None
    if isinstance(spec, DashboardSpec):
        dashboard_id = spec.dashboard.id
    existing = registry.dashboard(dashboard_id) if isinstance(dashboard_id, str) else None
    if existing is not None and not registry.can_edit(viewer, existing):
        return Report.of(
            [
                error(
                    "dashboard.forbidden",
                    "dashboard.id",
                    f"You may not change the dashboard {dashboard_id!r}.",
                    "Publish it under a new dashboard id, or ask its owner.",
                )
            ]
        ), None
    report = validate(spec, html)
    if not report.ok:
        return report, None
    return report, registry.save(DashboardSpec.model_validate(spec), html)
