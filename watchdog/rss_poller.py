"""
Poll the PyPI RSS feed for new releases.
Inserts unseen releases into the database and returns them for testing.
"""

import feedparser
import logging
from datetime import datetime
from watchdog import db
from watchdog.config import get

log = logging.getLogger(__name__)


def _parse_entry(entry) -> dict | None:
    """Extract release fields from a feed entry. Returns None if unparseable."""
    try:
        parts = entry.title.split()
        if len(parts) < 2:
            return None
        package = parts[0]
        version = parts[1]
        guid    = entry.get("id") or entry.get("link") or entry.title

        return {
            "id":       guid,
            "package":  package,
            "version":  version,
            "summary":  getattr(entry, "summary", None),
            "author":   getattr(entry, "author", None),
            "home_page": getattr(entry, "link", None),
        }
    except Exception as e:
        log.warning("Failed to parse feed entry: %s — %s", entry, e)
        return None


def _already_seen(conn, release_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM releases WHERE id = ?", (release_id,)
    ).fetchone()
    return row is not None


def poll() -> list[dict]:
    """
    Fetch PyPI RSS feed, insert new releases, return list of new release dicts.
    Each dict has keys: id, package, version, summary, author, home_page.
    """
    cfg  = get("watchdog")
    url  = cfg["rss_url"]

    log.info("Polling PyPI RSS: %s", url)
    feed = feedparser.parse(url)

    if feed.bozo:
        log.warning("Feed parse warning: %s", feed.bozo_exception)

    new_releases = []

    with db.transaction() as conn:
        for entry in feed.entries:
            release = _parse_entry(entry)
            if release is None:
                continue

            if _already_seen(conn, release["id"]):
                log.debug("Already seen: %s %s", release["package"], release["version"])
                continue

            conn.execute("""
                INSERT INTO releases (id, package, version, summary, author, home_page)
                VALUES (:id, :package, :version, :summary, :author, :home_page)
            """, release)

            new_releases.append(release)
            log.info("New release: %s %s", release["package"], release["version"])

    log.info("Poll complete — %d new releases", len(new_releases))
    return new_releases
