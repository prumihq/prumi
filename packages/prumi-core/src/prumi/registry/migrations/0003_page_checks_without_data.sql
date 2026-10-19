-- In-browser number checks without company data: the filter state is redacted and keyed by
-- a hash of the full state, flagged numbers are counted per code and element path, and error
-- messages are cleaned. Earlier reports held the values, so they're dropped; the next
-- viewer's check reports again.

DROP TABLE page_checks;

CREATE TABLE page_checks (
    dashboard_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    state_key TEXT NOT NULL,     -- sha256 (hex) of the full filter state, canonical JSON
    filters_json TEXT NOT NULL,  -- time filters as sent, dimension filters as {"selected": n}
    flagged_json TEXT NOT NULL,  -- [{code, path, count}]: numbers Prumi didn't deliver
    canvas INTEGER NOT NULL,
    errors_json TEXT NOT NULL,   -- [message], cleaned of dimension values and long numbers
    reported_at TEXT NOT NULL,
    PRIMARY KEY (dashboard_id, version, state_key),
    FOREIGN KEY (dashboard_id, version) REFERENCES versions (dashboard_id, version)
);
