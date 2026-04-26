"""
script_builder.py — Read test data from SQLite, produce a broadcast script.

Usage:
    python -m writer.script_builder [--db PATH] [--out PATH] [--hours N] [--dry-run]

Outputs:
    <out>.txt   — plain text, TTS-ready (markers stripped)
    <out>.json  — structured segments for timing/composition

The lookback window (--hours, default 24) controls which test results are
included. Pass --hours 0 to include all results ever recorded.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from writer import templates

_DEFAULT_DB  = "/opt/vps-pypi-place/data/pypi_place.db"
_DEFAULT_OUT = "/opt/vps-pypi-place/reports/script"
_DEFAULT_HOURS = 24


# ─────────────────────────────────────────────
# QUERIES
# ─────────────────────────────────────────────

def _since_clause(hours: int) -> str:
    if hours <= 0:
        return "1=1"
    return f"t.tested_at > datetime('now', '-{hours} hours')"


def _collect(conn, hours: int) -> dict:
    since = _since_clause(hours)
    c = conn.cursor()

    # ── batch overview
    c.execute(f"""
        SELECT
            COUNT(DISTINCT t.release_id)          AS packages_tested,
            COUNT(*)                               AS total_tests,
            SUM(t.status = 'PASS')                AS pass_count,
            SUM(t.status = 'FAIL')                AS fail_count,
            SUM(t.status = 'TIMEOUT')             AS timeout_count,
            SUM(t.status = 'PHANTOM')             AS phantom_count,
            SUM(t.compile_triggered = 1)          AS compile_count,
            MAX(t.tested_at)                      AS last_tested
        FROM test_results t
        WHERE {since}
          AND t.status != 'pending'
    """)
    row = c.fetchone()
    overview = {
        "packages_tested": row[0] or 0,
        "total_tests":     row[1] or 0,
        "pass_count":      row[2] or 0,
        "fail_count":      row[3] or 0,
        "timeout_count":   row[4] or 0,
        "phantom_count":   row[5] or 0,
        "compile_count":   row[6] or 0,
        "last_tested":     row[7],
    }
    total = overview["total_tests"] or 1
    overview["pass_rate"] = round(100.0 * overview["pass_count"] / total, 1)

    # ── arch split count (asymmetries in window)
    c.execute(f"""
        SELECT COUNT(DISTINCT a.release_id)
        FROM asymmetries a
        WHERE a.detected_at > datetime('now', '-{max(hours, 24)} hours')
    """)
    overview["arch_split_count"] = (c.fetchone()[0] or 0)

    # ── heaviest package (most deps)
    c.execute(f"""
        SELECT r.package, r.version, t.dep_count
        FROM test_results t
        JOIN releases r ON r.id = t.release_id
        WHERE {since}
          AND t.dep_count IS NOT NULL
          AND t.dep_count > 0
        ORDER BY t.dep_count DESC
        LIMIT 1
    """)
    row = c.fetchone()
    overview["heaviest_package"]   = row[0] if row else None
    overview["heaviest_version"]   = row[1] if row else None
    overview["heaviest_dep_count"] = row[2] if row else 0

    # ── top clean passes (passed ALL envs, ordered by dep_count ASC then install_ms ASC)
    c.execute(f"""
        SELECT
            r.package, r.version, r.summary, r.author,
            MIN(t.dep_count)                       AS dep_count,
            MIN(t.install_ms)                      AS install_ms,
            COUNT(DISTINCT t.environment)          AS env_count,
            SUM(t.status = 'PASS')                 AS pass_envs,
            COUNT(*)                               AS total_envs,
            GROUP_CONCAT(DISTINCT t.environment)   AS environments
        FROM test_results t
        JOIN releases r ON r.id = t.release_id
        WHERE {since}
          AND t.status != 'pending'
        GROUP BY t.release_id
        HAVING pass_envs = total_envs
        ORDER BY dep_count ASC NULLS LAST, install_ms ASC NULLS LAST
        LIMIT 5
    """)
    cols = ["package","version","summary","author","dep_count","install_ms",
            "env_count","pass_envs","total_envs","environments"]
    passes = [dict(zip(cols, r)) for r in c.fetchall()]

    # ── notable failures (one row per release, worst failure type)
    c.execute(f"""
        SELECT
            r.package, r.version, r.summary,
            t.status, t.failure_type,
            t.dep_count,
            GROUP_CONCAT(DISTINCT t.environment) AS environments
        FROM test_results t
        JOIN releases r ON r.id = t.release_id
        WHERE {since}
          AND t.status IN ('FAIL', 'TIMEOUT')
        GROUP BY t.release_id
        ORDER BY
            CASE t.failure_type
                WHEN 'build_failure'       THEN 1
                WHEN 'version_incompatible' THEN 2
                WHEN 'missing_dep'         THEN 3
                WHEN 'timeout'             THEN 4
                ELSE 5
            END,
            t.dep_count DESC NULLS LAST
        LIMIT 4
    """)
    cols = ["package","version","summary","status","failure_type","dep_count","environments"]
    failures = [dict(zip(cols, r)) for r in c.fetchall()]

    # ── phantoms
    c.execute(f"""
        SELECT
            r.package, r.version, r.summary,
            GROUP_CONCAT(DISTINCT t.environment) AS environments
        FROM test_results t
        JOIN releases r ON r.id = t.release_id
        WHERE {since}
          AND t.status = 'PHANTOM'
        GROUP BY t.release_id
        LIMIT 5
    """)
    cols = ["package","version","summary","environments"]
    phantoms = [dict(zip(cols, r)) for r in c.fetchall()]

    # ── asymmetries (recent window)
    c.execute(f"""
        SELECT
            r.package, r.version, r.summary,
            a.asymmetry_type, a.passing_envs, a.failing_envs, a.detail
        FROM asymmetries a
        JOIN releases r ON r.id = a.release_id
        WHERE a.detected_at > datetime('now', '-{max(hours, 24)} hours')
        ORDER BY a.detected_at DESC
        LIMIT 5
    """)
    cols = ["package","version","summary","asymmetry_type","passing_envs","failing_envs","detail"]
    asymmetries = [dict(zip(cols, r)) for r in c.fetchall()]

    # parse JSON env lists
    for a in asymmetries:
        a["passing_envs"] = _parse_json_list(a["passing_envs"])
        a["failing_envs"] = _parse_json_list(a["failing_envs"])

    return {
        "overview":    overview,
        "passes":      passes,
        "failures":    failures,
        "phantoms":    phantoms,
        "asymmetries": asymmetries,
        "date":        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _parse_json_list(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else [str(v)]
    except (json.JSONDecodeError, TypeError):
        # comma-separated fallback (GROUP_CONCAT format)
        return [x.strip() for x in str(raw).split(",") if x.strip()]


# ─────────────────────────────────────────────
# ASSEMBLY
# ─────────────────────────────────────────────

def build_segments(data: dict) -> list[dict]:
    """
    Return a list of segment dicts:
        {"type": str, "label": str, "text": str}
    Order matches broadcast sequence.
    """
    ov = data["overview"]
    segments = []

    def seg(type_, label, text):
        segments.append({"type": type_, "label": label, "text": text.strip()})

    seg("opener", "opener",
        templates.opener(data["date"], ov))

    seg("numbers", "numbers",
        templates.numbers(ov))

    if data["passes"]:
        seg("passes_intro", "passes_intro",
            templates.passes_intro(len(data["passes"])))
        for pkg in data["passes"]:
            seg("package_pass", f"pass:{pkg['package']}",
                templates.package_pass(pkg))

    if data["failures"]:
        seg("failures_intro", "failures_intro",
            templates.failures_intro(len(data["failures"])))
        for pkg in data["failures"]:
            seg("package_fail", f"fail:{pkg['package']}",
                templates.package_fail(pkg))

    if data["phantoms"]:
        seg("phantoms_intro", "phantoms_intro",
            templates.phantoms_intro())
        for pkg in data["phantoms"]:
            seg("package_phantom", f"phantom:{pkg['package']}",
                templates.package_phantom(pkg))

    if data["asymmetries"]:
        seg("asymmetries_intro", "asymmetries_intro",
            templates.asymmetries_intro())
        for a in data["asymmetries"]:
            seg("asymmetry", f"asym:{a['package']}",
                templates.asymmetry(a))

    seg("commercial", "commercial",
        templates.commercial())

    seg("signoff", "signoff",
        templates.signoff(data["date"], ov))

    return segments


def segments_to_text(segments: list[dict]) -> str:
    return "\n\n".join(s["text"] for s in segments)


# ─────────────────────────────────────────────
# REPORT BATCH RECORD
# ─────────────────────────────────────────────

def _record_batch(conn, date: str, ov: dict):
    """Write or update the report_batches row for today."""
    conn.execute("""
        INSERT INTO report_batches (
            date, status,
            releases_tested, pass_count, fail_count, timeout_count, phantom_count,
            build_failures, arch_splits,
            heaviest_package, heaviest_dep_count
        ) VALUES (?, 'writing', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            status           = 'writing',
            releases_tested  = excluded.releases_tested,
            pass_count       = excluded.pass_count,
            fail_count       = excluded.fail_count,
            timeout_count    = excluded.timeout_count,
            phantom_count    = excluded.phantom_count,
            build_failures   = excluded.build_failures,
            arch_splits      = excluded.arch_splits,
            heaviest_package = excluded.heaviest_package,
            heaviest_dep_count = excluded.heaviest_dep_count
    """, (
        date,
        ov["packages_tested"],
        ov["pass_count"],
        ov["fail_count"],
        ov["timeout_count"],
        ov["phantom_count"],
        ov["compile_count"],
        ov["arch_split_count"],
        ov["heaviest_package"],
        ov["heaviest_dep_count"],
    ))


def _record_report(conn, date: str, prose_path: str, word_count: int) -> int:
    """Insert or update the reports row. Returns the report id."""
    c = conn.execute("""
        INSERT INTO reports (date, prose_path, word_count, status)
        VALUES (?, ?, ?, 'complete')
        ON CONFLICT(date) DO UPDATE SET
            prose_path = excluded.prose_path,
            word_count = excluded.word_count,
            status     = 'complete'
        RETURNING id
    """, (date, prose_path, word_count))
    row = c.fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

def run(db_path: str, out_stem: str, hours: int, dry_run: bool = False) -> int:
    db_file = Path(db_path)
    if not db_file.exists():
        print(f"script_builder: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    try:
        data = _collect(conn, hours)
    finally:
        conn.close()

    ov = data["overview"]
    if ov["packages_tested"] == 0:
        print("script_builder: no test results in window — nothing to write", file=sys.stderr)
        return 0

    segments = build_segments(data)
    script_text = segments_to_text(segments)
    word_count = len(script_text.split())

    out_txt  = Path(out_stem + ".txt")
    out_json = Path(out_stem + ".json")

    if dry_run:
        print(f"--- DRY RUN ({word_count} words, ~{word_count // 130} min) ---")
        print(script_text)
        return 0

    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text(script_text, encoding="utf-8")
    out_json.write_text(
        json.dumps({"date": data["date"], "generated_at": data["generated_at"],
                    "word_count": word_count, "segments": segments}, indent=2),
        encoding="utf-8",
    )

    # Record in DB
    conn = sqlite3.connect(str(db_file))
    try:
        with conn:
            _record_batch(conn, data["date"], ov)
            _record_report(conn, data["date"], str(out_txt), word_count)
    finally:
        conn.close()

    print(f"script_builder: {word_count} words → {out_txt}")
    print(f"script_builder: segments  → {out_json}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Build The PyPI Place broadcast script")
    p.add_argument("--db",    default=_DEFAULT_DB,    help="SQLite DB path")
    p.add_argument("--out",   default=_DEFAULT_OUT,   help="Output path stem (no extension)")
    p.add_argument("--hours", default=_DEFAULT_HOURS, type=int,
                   help="Lookback window in hours (0 = all time)")
    p.add_argument("--dry-run", action="store_true",  help="Print script to stdout, no writes")
    args = p.parse_args()
    sys.exit(run(args.db, args.out, args.hours, args.dry_run))


if __name__ == "__main__":
    main()
