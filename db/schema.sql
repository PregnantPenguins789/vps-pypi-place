-- vps-pypi-place database schema
-- SQLite, WAL mode, single file
-- All components read/write through this schema

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─────────────────────────────────────────────
-- INGESTION
-- ─────────────────────────────────────────────

-- Every release seen on the PyPI RSS feed
CREATE TABLE IF NOT EXISTS releases (
    id          TEXT PRIMARY KEY,   -- PyPI release GUID from RSS
    package     TEXT NOT NULL,
    version     TEXT NOT NULL,
    summary     TEXT,               -- from RSS description
    author      TEXT,
    home_page   TEXT,
    first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- One row per release × environment combination
CREATE TABLE IF NOT EXISTS test_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id      TEXT NOT NULL REFERENCES releases(id),
    environment     TEXT NOT NULL,  -- e.g. "python:3.11-slim"
    arch            TEXT NOT NULL,  -- e.g. "arm64", "amd64"
    status          TEXT NOT NULL,  -- PASS | FAIL | TIMEOUT | SKIP
    exit_code       INTEGER,
    error_log       TEXT,           -- last 2000 chars of stderr
    install_ms      INTEGER,        -- install duration in milliseconds
    tested_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(release_id, environment, arch)
);

-- ─────────────────────────────────────────────
-- ANALYSIS
-- ─────────────────────────────────────────────

-- Daily report batches (input to writing_machine)
CREATE TABLE IF NOT EXISTS report_batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | writing | complete | failed
    releases_tested INTEGER,
    pass_count  INTEGER,
    fail_count  INTEGER,
    timeout_count INTEGER,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Generated report prose output
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES report_batches(id),
    date        TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD
    prose_path  TEXT,                   -- path to .md file
    word_count  INTEGER,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | complete | failed
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Notable findings extracted from test results (feed into writer)
CREATE TABLE IF NOT EXISTS findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id      TEXT NOT NULL REFERENCES releases(id),
    finding_type    TEXT NOT NULL,  -- always_fails | arch_specific | import_fails | dep_conflict | clean_pass
    detail          TEXT,           -- human-readable detail extracted from error_log
    environments    TEXT,           -- JSON list of affected environments
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- BROADCAST
-- ─────────────────────────────────────────────

-- One episode per day
CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD
    report_id       INTEGER REFERENCES reports(id),
    audio_path      TEXT,
    video_path      TEXT,
    duration_sec    INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | rendering | complete | failed
    published_at    DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- INDEXES
-- ─────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_test_results_release    ON test_results(release_id);
CREATE INDEX IF NOT EXISTS idx_test_results_status     ON test_results(status);
CREATE INDEX IF NOT EXISTS idx_test_results_tested_at  ON test_results(tested_at);
CREATE INDEX IF NOT EXISTS idx_findings_release        ON findings(release_id);
CREATE INDEX IF NOT EXISTS idx_findings_type           ON findings(finding_type);
CREATE INDEX IF NOT EXISTS idx_releases_first_seen     ON releases(first_seen);
