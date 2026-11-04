-- In-browser number checks without company data, as on SQLite; the columns the registry
-- sorts by use the "C" collation, so they sort by byte, as on SQLite.

DROP TABLE page_checks;

CREATE TABLE page_checks (
    dashboard_id TEXT COLLATE "C" NOT NULL,
    version INTEGER NOT NULL,
    state_key TEXT COLLATE "C" NOT NULL,  -- sha256 (hex) of the full filter state
    filters_json TEXT NOT NULL,  -- time filters as sent, dimension filters as {"selected": n}
    flagged_json TEXT NOT NULL,  -- [{code, path, count}]: numbers Prumi didn't deliver
    canvas INTEGER NOT NULL,
    errors_json TEXT NOT NULL,   -- [message], cleaned of dimension values and long numbers
    reported_at TEXT COLLATE "C" NOT NULL,
    PRIMARY KEY (dashboard_id, version, state_key),
    FOREIGN KEY (dashboard_id, version) REFERENCES versions (dashboard_id, version)
);
