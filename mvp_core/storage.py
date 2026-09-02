"""阶段一 SQLite 持久化：原始消息档案和幂等去重。"""

import hashlib
import json
import sqlite3
from pathlib import Path
from threading import RLock

from .message import UnifiedMessage


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                message_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_group INTEGER NOT NULL,
                mentioned_bot INTEGER NOT NULL,
                reply_to_bot INTEGER NOT NULL,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
                ON messages(conversation_id, created_at);
            """
        )
        if self._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            self._conn.execute("INSERT INTO schema_version(version) VALUES (1)")
        self._conn.commit()

    @staticmethod
    def fallback_key(message: UnifiedMessage) -> str:
        payload = "\x1f".join(
            [message.conversation_id, message.sender_id or "", message.iso_created_at(), message.message_type.value, message.content]
        )
        return f"fallback:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    def record_message(self, message: UnifiedMessage) -> bool:
        key = message.dedup_key() if message.message_id else self.fallback_key(message)
        with self._lock:
            try:
                self._conn.execute(
                    """INSERT INTO messages
                    (dedup_key, conversation_id, sender_id, sender_name, message_type,
                     content, created_at, is_group, mentioned_bot, reply_to_bot, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, message.conversation_id, message.sender_id, message.sender_name,
                     message.message_type.value, message.content, message.iso_created_at(),
                     int(message.is_group), int(message.mentioned_bot), int(message.reply_to_bot),
                     json.dumps(message.raw, ensure_ascii=False, default=str)),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False

    def count_messages(self, conversation_id: str | None = None) -> int:
        with self._lock:
            if conversation_id is None:
                row = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
