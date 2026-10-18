-- The latest in-browser number check per version and filter state.

CREATE TABLE page_checks (
    dashboard_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    filters_json TEXT NOT NULL,  -- the filter state, canonical JSON
    flagged_json TEXT NOT NULL,  -- [{text, path}]: numbers Prumi didn't deliver
    canvas INTEGER NOT NULL,
    errors_json TEXT NOT NULL,   -- [message]
    reported_at TEXT NOT NULL,
    PRIMARY KEY (dashboard_id, version, filters_json),
    FOREIGN KEY (dashboard_id, version) REFERENCES versions (dashboard_id, version)
);
