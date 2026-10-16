"""Idempotent processing: the same file content processed twice must not
create duplicate records. Keyed by a content hash, not a filename -- a
renamed or re-uploaded copy of the same bytes is still the same invoice,
and two genuinely different invoices that happen to share a filename are
not. SQLite gives crash-safe, transactional writes for free, consistent
with this project's atomic-write approach elsewhere (see
invoice_agent.utils.atomic_write_json)."""
import hashlib
import json
import sqlite3
from contextlib import closing


class IdempotencyStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed (
                    content_hash TEXT PRIMARY KEY,
                    source_name  TEXT,
                    outcome_json TEXT NOT NULL,
                    processed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    @staticmethod
    def hash_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def get(self, content_hash: str):
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT outcome_json FROM processed WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def record(self, content_hash: str, source_name: str, outcome: dict) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO processed (content_hash, source_name, outcome_json) "
                "VALUES (?, ?, ?)",
                (content_hash, source_name, json.dumps(outcome)),
            )
            conn.commit()

    def process_once(self, content: bytes, source_name: str, compute_fn):
        """Run compute_fn() only if this exact content hasn't been seen
        before; otherwise return the cached outcome. Returns (outcome_dict,
        was_cached: bool)."""
        content_hash = self.hash_bytes(content)
        cached = self.get(content_hash)
        if cached is not None:
            return cached, True
        outcome = compute_fn()
        self.record(content_hash, source_name, outcome)
        return outcome, False
