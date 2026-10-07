"""One local SQLite store for cache entries and resumable graph snapshots."""

import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, "
            "value BLOB NOT NULL, expires REAL NOT NULL)"
        )

        self.db.execute("CREATE INDEX IF NOT EXISTS expiry ON entries(expires)")

    def get(self, key: str):
        row = self.db.execute("SELECT value, expires FROM entries WHERE key=?", (key,)).fetchone()
        return row[0] if row and row[1] > time.time() else None

    def put(self, key: str, value: bytes, ttl: float = 86400):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?)", (key, value, time.time() + ttl)
            )
            self.db.execute("DELETE FROM entries WHERE expires < ?", (time.time(),))

    def load(self, key: str):
        data = self.get(key)
        return json.loads(data) if data is not None else None

    def save(self, key: str, value, ttl: float = 86400 * 90):
        self.put(key, json.dumps(value, ensure_ascii=False).encode(), ttl)

    def close(self):
        self.db.close()
