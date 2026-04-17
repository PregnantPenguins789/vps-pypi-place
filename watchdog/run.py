"""
Watchdog entry point.
Poll RSS → test new releases → classify findings.
Designed to be called by systemd timer.
"""

import logging
import sys
from pathlib import Path
from watchdog import db
from watchdog import rss_poller
from watchdog import batch_runner
from watchdog.config import get

def setup_logging():
    cfg = get("logging")
    logging.basicConfig(
        level=getattr(logging, cfg.get("level", "INFO")),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

def main():
    setup_logging()
    log = logging.getLogger("watchdog.run")

    log.info("=== watchdog starting ===")

    # Ensure database exists
    schema = Path(__file__).parent.parent / "db" / "schema.sql"
    db.init(schema)

    # Poll for new releases
    new_releases = rss_poller.poll()

    if not new_releases:
        log.info("No new releases found. Done.")
        return

    # Test them
    batch_runner.run_batch(new_releases)

    log.info("=== watchdog done ===")

if __name__ == "__main__":
    main()
