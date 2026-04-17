from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class MemoryManager:
    """SQLite-backed conversation memory for AIRD."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            # Some restricted environments cannot create WAL sidecar files.
            conn.execute("PRAGMA journal_mode=DELETE;")
        try:
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        session_id TEXT,
                        user_message TEXT NOT NULL,
                        ai_response TEXT NOT NULL,
                        agent_used TEXT,
                        provider_id TEXT,
                        model TEXT,
                        metadata_json TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversations_agent_used ON conversations(agent_used)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id)"
                )

    def save_conversation(
        self,
        user_message: str,
        ai_response: str,
        *,
        session_id: str = "",
        agent_used: str = "",
        provider_id: str = "",
        model: str = "",
        metadata_json: str = "",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO conversations (
                        timestamp, session_id, user_message, ai_response, agent_used, provider_id, model, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        str(session_id or "").strip(),
                        str(user_message or ""),
                        str(ai_response or ""),
                        str(agent_used or "").strip().lower(),
                        str(provider_id or "").strip().lower(),
                        str(model or "").strip(),
                        str(metadata_json or ""),
                    ),
                )
                return int(cur.lastrowid or 0)

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        use_limit = max(1, min(int(limit or 20), 500))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, timestamp, session_id, user_message, ai_response, agent_used, provider_id, model, metadata_json
                    FROM conversations
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (use_limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def search_history(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q = str(query or "").strip()
        if not q:
            return []
        use_limit = max(1, min(int(limit or 20), 500))
        pattern = f"%{q}%"
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, timestamp, session_id, user_message, ai_response, agent_used, provider_id, model, metadata_json
                    FROM conversations
                    WHERE user_message LIKE ? OR ai_response LIKE ? OR agent_used LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, pattern, use_limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def clear_history(self) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM conversations")
                return int(cur.rowcount or 0)
