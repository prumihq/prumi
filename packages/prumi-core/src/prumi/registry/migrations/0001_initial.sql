-- Registry schema, version 1.

CREATE TABLE dashboards (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL,
    current_version INTEGER NOT NULL
);

-- Versions are immutable: rows are only ever inserted (and deleted with their dashboard).
CREATE TABLE versions (
    dashboard_id TEXT NOT NULL REFERENCES dashboards (id),
    version INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    html TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    html_hash TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (dashboard_id, version)
);

CREATE TABLE certifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dashboard_id TEXT NOT NULL REFERENCES dashboards (id),
    version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    certified_by TEXT NOT NULL,
    certified_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_reason TEXT
);
CREATE INDEX certifications_by_fingerprint ON certifications (dashboard_id, fingerprint);

-- No foreign key on dashboard_id: an agent may request a metric before the dashboard exists.
CREATE TABLE metric_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    reason TEXT NOT NULL,
    dashboard_id TEXT,
    requested_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
