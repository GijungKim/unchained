-- Unchained Health store. One row per measurement; waveforms stored per session.
-- Identity is (device_id, metric, timestamp, method): the same moment read two
-- ways is two rows, so the agent can compare decode paths, not just devices.

CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY,
    device_id   TEXT NOT NULL,          -- whoop-5, duo-camera
    metric      TEXT NOT NULL,          -- heart_rate, skin_temp, hrv_rmssd, ...
    timestamp   TEXT NOT NULL,          -- UTC ISO 8601, Zulu
    value       REAL NOT NULL,
    unit        TEXT NOT NULL,          -- bpm, celsius, ms, ...
    quality     REAL NOT NULL,          -- 0..1 signal confidence, always present
    method      TEXT NOT NULL,          -- exact decode path, e.g. whoop5-hrs-broadcast
    session_id  TEXT,                   -- groups one capture window (nullable)
    UNIQUE (device_id, metric, timestamp, method)
);
CREATE INDEX IF NOT EXISTS idx_records_lookup
    ON records (device_id, metric, timestamp);

CREATE TABLE IF NOT EXISTS waveforms (
    id              INTEGER PRIMARY KEY,
    device_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,      -- window start, UTC ISO Zulu
    kind            TEXT NOT NULL,      -- ppg_raw, ibi_series, ...
    samples_json    TEXT NOT NULL,      -- JSON array of numbers
    sample_rate_hz  REAL,
    unit            TEXT NOT NULL,
    UNIQUE (device_id, session_id, timestamp, kind)
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,       -- caller-chosen, e.g. whoop5-20261102-1200
    device_id   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    source      TEXT NOT NULL,          -- live | replay | import
    notes       TEXT
);
