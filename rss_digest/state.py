import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS keys (key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS page_hashes (page TEXT PRIMARY KEY, hash TEXT NOT NULL);
"""


class State:
    """Per-instance SQLite state. Every write commits at once so an interrupted run can resume."""

    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def _one(self, sql: str, arg: str):
        row = self.db.execute(sql, (arg,)).fetchone()
        return row[0] if row else None

    def _write(self, sql: str, args: tuple):
        with self.db:
            self.db.execute(sql, args)

    def is_processed(self, item_id: str) -> bool:
        return self._one("SELECT 1 FROM processed WHERE id = ?", item_id) is not None

    def mark_processed(self, item_id: str) -> None:
        self._write("INSERT OR IGNORE INTO processed VALUES (?)", (item_id,))

    def has_key(self, key: str) -> bool:
        return self._one("SELECT 1 FROM keys WHERE key = ?", key) is not None

    def add_key(self, key: str) -> None:
        self._write("INSERT OR IGNORE INTO keys VALUES (?)", (key,))

    def get(self, name: str) -> str | None:
        return self._one("SELECT value FROM meta WHERE name = ?", name)

    def set(self, name: str, value: str) -> None:
        self._write("INSERT OR REPLACE INTO meta VALUES (?, ?)", (name, value))

    def page_hash(self, page: str) -> str | None:
        return self._one("SELECT hash FROM page_hashes WHERE page = ?", page)

    def set_page_hash(self, page: str, h: str) -> None:
        self._write("INSERT OR REPLACE INTO page_hashes VALUES (?, ?)", (page, h))
