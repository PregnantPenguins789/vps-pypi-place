"""
adapter.py — bridge between vps-pypi-place SQLite and YggCrawl outbox

Reads test_results (joined with releases) from the local DB, writes one
JSON file per result to an outbox directory that yggcrawl's ingest.py
reads.  Uses a watermark file to avoid re-exporting.

Usage:
    python -m writer.adapter [--outbox DIR] [--db PATH] [--since ISO_TS]

Outbox record shape (matches yggcrawl ingest._valid_pypi_record):
    {
        "kind":         "pypi_test_result",
        "package":      str,
        "version":      str,
        "environment":  str,
        "result":       str,   # PASS | FAIL | TIMEOUT | PHANTOM | PARTIAL
        "observed_at":  str,   # ISO-8601
        "result_hash":  str,   # SHA-256 of canonical fields
        "arch":         str,
        "failure_type": str | null,
        "dep_count":    int | null,
    }
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DB      = "/opt/vps-pypi-place/data/pypi_place.db"
_DEFAULT_OUTBOX  = "/opt/vps-pypi-place/data/outbox"
_WATERMARK_FILE  = "adapter_watermark.txt"
_EPOCH           = "1970-01-01T00:00:00"


def _result_hash(package: str, version: str, environment: str,
                 result: str, observed_at: str) -> str:
    canonical = f"{package}\x00{version}\x00{environment}\x00{result}\x00{observed_at}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def _load_watermark(outbox: Path) -> str:
    wf = outbox / _WATERMARK_FILE
    if wf.exists():
        return wf.read_text().strip() or _EPOCH
    return _EPOCH


def _save_watermark(outbox: Path, ts: str) -> None:
    (outbox / _WATERMARK_FILE).write_text(ts)


def _query_results(db_path: str, since: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT
                r.package,
                r.version,
                t.environment,
                t.arch,
                t.status,
                t.failure_type,
                t.dep_count,
                t.tested_at
            FROM test_results t
            JOIN releases r ON r.id = t.release_id
            WHERE t.tested_at > ?
              AND t.status != 'pending'
            ORDER BY t.tested_at ASC
            """,
            (since,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def _to_record(row) -> dict:
    result = (row["status"] or "FAIL").upper()
    observed_at = row["tested_at"]

    return {
        "kind":         "pypi_test_result",
        "package":      row["package"],
        "version":      row["version"],
        "environment":  row["environment"],
        "result":       result,
        "observed_at":  observed_at,
        "result_hash":  _result_hash(
            row["package"], row["version"],
            row["environment"], result, observed_at,
        ),
        "arch":         row["arch"],
        "failure_type": row["failure_type"],
        "dep_count":    row["dep_count"],
    }


def run(db_path: str, outbox_dir: str, since: str | None = None) -> int:
    outbox = Path(outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)

    watermark = since or _load_watermark(outbox)
    rows = _query_results(db_path, watermark)

    if not rows:
        print(f"adapter: no new results since {watermark}")
        return 0

    latest_ts = watermark
    written = 0

    for row in rows:
        record = _to_record(row)
        fname = (
            f"{record['package']}-{record['version']}-"
            f"{record['environment'].replace(':', '_').replace('/', '-')}-"
            f"{record['result_hash'][:12]}.json"
        )
        dest = outbox / fname
        if not dest.exists():
            dest.write_text(json.dumps(record, indent=2), encoding="utf-8")
            written += 1

        if record["observed_at"] > latest_ts:
            latest_ts = record["observed_at"]

    if latest_ts != watermark:
        _save_watermark(outbox, latest_ts)

    print(f"adapter: wrote {written} records to {outbox}  (watermark → {latest_ts})")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Export vps-pypi-place results to YggCrawl outbox")
    parser.add_argument("--db",     default=_DEFAULT_DB,     help="SQLite DB path")
    parser.add_argument("--outbox", default=_DEFAULT_OUTBOX, help="Outbox directory")
    parser.add_argument("--since",  default=None,
                        help="Override watermark (ISO-8601 timestamp)")
    args = parser.parse_args()
    sys.exit(run(args.db, args.outbox, args.since))


if __name__ == "__main__":
    main()
