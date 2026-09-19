"""SQLite cache of raw System One answers, keyed by everything that affects them.

The key hashes the exact state, the question definitions, and the model name,
so editing a question or a segment (or the one before it) misses the cache,
while tuning composition constants never does.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "system_one.sqlite"


class AnswerCache:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._db.commit()

    @staticmethod
    def key(state: dict, questions_fp: str, model: str) -> str:
        blob = json.dumps({"s": state, "q": questions_fp, "m": model}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        row = self._db.execute("SELECT value FROM answers WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO answers (key, value) VALUES (?, ?)", (key, json.dumps(value))
        )
        self._db.commit()
