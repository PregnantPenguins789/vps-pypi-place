import sqlite3
from pathlib import Path
from contextlib import contextmanager
from watchdog.config import get

def _db_path() -> str:
    return get("database")["path"]

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

@contextmanager
def transaction():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init(schema_path: Path | None = None):
    """Initialize database from schema.sql — idempotent."""
    if schema_path is None:
        schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
    sql = schema_path.read_text()
    with transaction() as conn:
        conn.executescript(sql)
