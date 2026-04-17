-- vps-pypi-place database schema
-- SQLite, WAL mode, single file
-- All components read/write through this schema
--
-- Design principle: the install step is the choke point where truth enters.
-- Every downstream component inherits credibility from what happens here.
-- We do not store outcomes. We store fingerprints.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─────────────────────────────────────────────
-- INGESTION
-- ─────────────────────────────────────────────

-- Every release seen on the PyPI RSS feed
CREATE TABLE IF NOT EXISTS releases (
    id          TEXT PRIMARY KEY,       -- PyPI release GUID from RSS
    package     TEXT NOT NULL,
    version     TEXT NOT NULL,
    summary     TEXT,                   -- from RSS description
    author      TEXT,
    home_page   TEXT,
    first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- TESTING — four-phase fingerprint
-- ─────────────────────────────────────────────

-- One row per release × environment × arch
-- Each phase is tested independently and logged separately.
--
-- Phases:
--   download   pip download (resolution only — is metadata sane?)
--   nodeps     pip install --no-deps (is the package itself intact?)
--   full       pip install (does the dependency graph resolve?)
--   import     python -c "import X" (does it exist at runtime?)
--
-- A package that passes full but fails import is a phantom success.
-- That is a named, reportable finding.

CREATE TABLE IF NOT EXISTS test_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id      TEXT NOT NULL REFERENCES releases(id),
    environment     TEXT NOT NULL,      -- e.g. "python:3.12-slim"
    arch            TEXT NOT NULL,      -- e.g. "arm64", "amd64"

    -- Phase results
    phase_download  TEXT,               -- PASS | FAIL | SKIP
    phase_nodeps    TEXT,               -- PASS | FAIL | SKIP
    phase_full      TEXT,               -- PASS | FAIL | TIMEOUT | SKIP
    phase_import    TEXT,               -- PASS | FAIL | SKIP

    -- Overall derived status (set after all phases complete)
    -- PASS        = all four phases pass
    -- PHANTOM     = full passes, import fails
    -- FAIL        = one of download/nodeps/full fails
    -- TIMEOUT     = phase_full timed out
    -- PARTIAL     = some phases skipped due to prior failure
    status          TEXT NOT NULL DEFAULT 'pending',

    -- Failure classification (null if status = PASS)
    -- build_failure       C/C++/Rust compile step failed
    -- missing_dep         dependency not resolvable
    -- bad_metadata        malformed package metadata
    -- version_incompatible Python version constraint violated
    -- network_failure     network error during install
    -- timeout             exceeded time limit
    -- import_error        installed but cannot be imported
    -- phantom             installs cleanly, imports nothing
    failure_type    TEXT,

    -- Dependency surface
    dep_count           INTEGER,        -- total packages installed
    wheel_count         INTEGER,        -- wheels used
    source_count        INTEGER,        -- source builds triggered
    compile_triggered   INTEGER,        -- 1 if any compilation occurred
    install_ms          INTEGER,        -- total install duration ms

    -- Phase error logs (last 1000 chars each)
    download_log    TEXT,
    nodeps_log      TEXT,
    full_log        TEXT,
    import_log      TEXT,

    tested_at       DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(release_id, environment, arch)
);

-- ─────────────────────────────────────────────
-- CLASSIFICATION
-- ─────────────────────────────────────────────

-- Behavioral archetype per package (derived from test_results, updated over time)
--
-- clean_citizen      installs cleanly everywhere, minimal deps
-- fragile_tower      installs only under narrow constraints
-- dep_explosion      pulls large dependency trees
-- phantom_success    installs, but unusable (import fails)
-- bitrot             fails broadly, likely abandoned
-- native_gamble      compilation-dependent, inconsistent across envs
-- arch_specific      passes x86, fails ARM64 or vice versa
-- unknown            insufficient data

CREATE TABLE IF NOT EXISTS package_archetypes (
    package         TEXT PRIMARY KEY,
    archetype       TEXT NOT NULL DEFAULT 'unknown',
    confidence      REAL DEFAULT 0.0,   -- 0.0–1.0, based on evidence count
    evidence_count  INTEGER DEFAULT 0,
    notes           TEXT,               -- human-readable summary of why
    last_updated    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Cross-environment asymmetries (the story the broadcast tells)
-- Populated when same package shows different results across envs
CREATE TABLE IF NOT EXISTS asymmetries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id      TEXT NOT NULL REFERENCES releases(id),
    asymmetry_type  TEXT NOT NULL,  -- arch_split | version_split | distro_split
    passing_envs    TEXT,           -- JSON list
    failing_envs    TEXT,           -- JSON list
    detail          TEXT,
    detected_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- ANALYSIS
-- ─────────────────────────────────────────────

-- Daily report batches
CREATE TABLE IF NOT EXISTS report_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending | writing | complete | failed

    -- Counts for the day
    releases_tested     INTEGER DEFAULT 0,
    pass_count          INTEGER DEFAULT 0,
    fail_count          INTEGER DEFAULT 0,
    timeout_count       INTEGER DEFAULT 0,
    phantom_count       INTEGER DEFAULT 0,

    -- Failure breakdown
    build_failures      INTEGER DEFAULT 0,
    missing_dep_failures INTEGER DEFAULT 0,
    import_failures     INTEGER DEFAULT 0,
    arch_splits         INTEGER DEFAULT 0,      -- asymmetries detected

    -- Dependency surface summary
    avg_dep_count       REAL,
    heaviest_package    TEXT,
    heaviest_dep_count  INTEGER,

    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Generated report prose
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES report_batches(id),
    date        TEXT NOT NULL UNIQUE,
    prose_path  TEXT,                   -- path to .md file
    word_count  INTEGER,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Notable findings fed to the writer as structured source material
CREATE TABLE IF NOT EXISTS findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id      TEXT NOT NULL REFERENCES releases(id),
    finding_type    TEXT NOT NULL,
    -- always_fails | arch_specific | phantom_success | dep_explosion
    -- import_fails | version_split | clean_pass | bitrot | native_gamble
    detail          TEXT,
    environments    TEXT,               -- JSON list of affected environments
    severity        TEXT DEFAULT 'info', -- info | notable | significant | critical
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- BROADCAST
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL UNIQUE,
    report_id       INTEGER REFERENCES reports(id),
    audio_path      TEXT,
    video_path      TEXT,
    duration_sec    INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',
    published_at    DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- INDEXES
-- ─────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_test_results_release     ON test_results(release_id);
CREATE INDEX IF NOT EXISTS idx_test_results_status      ON test_results(status);
CREATE INDEX IF NOT EXISTS idx_test_results_env         ON test_results(environment);
CREATE INDEX IF NOT EXISTS idx_test_results_arch        ON test_results(arch);
CREATE INDEX IF NOT EXISTS idx_test_results_failure     ON test_results(failure_type);
CREATE INDEX IF NOT EXISTS idx_test_results_tested_at   ON test_results(tested_at);
CREATE INDEX IF NOT EXISTS idx_findings_release         ON findings(release_id);
CREATE INDEX IF NOT EXISTS idx_findings_type            ON findings(finding_type);
CREATE INDEX IF NOT EXISTS idx_findings_severity        ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_asymmetries_release      ON asymmetries(release_id);
CREATE INDEX IF NOT EXISTS idx_releases_first_seen      ON releases(first_seen);
CREATE INDEX IF NOT EXISTS idx_releases_package         ON releases(package);
