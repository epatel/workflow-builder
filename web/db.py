"""SQLite access + stdlib migration runner (PRAGMA user_version)."""
import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "workflow.db"))
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate() -> int:
    """Apply pending NNN_*.sql files in order. Returns the final version."""
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    conn = connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for f in files:
            n = int(f.name[:3])
            if n <= version:
                continue
            conn.executescript(f.read_text())
            conn.execute(f"PRAGMA user_version = {n}")  # ponytail: n is an int from filename, safe to inline
            conn.commit()
            version = n
        return version
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"migrated {DB_PATH} -> version {migrate()}")
