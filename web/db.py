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
        latest = max((int(f.name[:3]) for f in files), default=0)
        if version > latest:
            # Written by newer code (e.g. a rolled-back deploy): refuse rather than run old
            # code against a schema it doesn't know.
            raise RuntimeError(f"database {DB_PATH} is at schema version {version}, but this "
                               f"code only knows migrations up to {latest}: it was written by "
                               "newer code")
        for f in files:
            n = int(f.name[:3])
            if n <= version:
                continue
            # One transaction per migration: executescript() commits any pending transaction
            # first and ignores `with conn:`, so the BEGIN/COMMIT must live in the script itself
            # or a failing migration leaves the schema half-changed. n is an int from the filename.
            try:
                conn.executescript(f"BEGIN;\n{f.read_text()}\n;PRAGMA user_version = {n};\nCOMMIT;")
            except sqlite3.Error:
                if conn.in_transaction:
                    conn.rollback()
                raise
            version = n
        return version
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"migrated {DB_PATH} -> version {migrate()}")
