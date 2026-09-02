"""阶段一运行时桥接，供 wechatbot-new 的 message_listener 调用。"""

import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any

from .decision import Decision, DecisionEngine
from .permissions import PermissionService
from .pipeline import MessagePipeline, PipelineResult
from .storage import SQLiteStore
from .wechat_adapter import from_wechat


class StageOneRuntime:
    """惰性初始化 SQLite，避免导入 bot.py 时改变现有启动行为。"""

    def __init__(self, root: str | Path, *, keywords: tuple[str, ...] = ()):
        self.root = Path(root)
        self._lock = Lock()
        self._store: SQLiteStore | None = None
        self._permissions: PermissionService | None = None
        self._pipeline: MessagePipeline | None = None
        self.keywords = keywords

    def _ensure(self) -> MessagePipeline:
        with self._lock:
            if self._pipeline is None:
                data_dir = self.root / "data"
                data_dir.mkdir(parents=True, exist_ok=True)
                self._store = SQLiteStore(data_dir / "mvp_stage1.db")
                self._permissions = PermissionService(self._store._conn)
                self._pipeline = MessagePipeline(self._store, self._permissions, DecisionEngine(self.keywords))
            return self._pipeline

    def handle(self, message: Any, chat: Any, *, bot_name: str | None = None, auto_reply: bool = True) -> PipelineResult:
        if getattr(message, "attr", None) == "self":
            return PipelineResult(Decision.IGNORE, False, "self_message")
        return self._ensure().handle(from_wechat(message, chat, bot_name=bot_name), auto_reply=auto_reply)

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
            self._store = None
            self._permissions = None
            self._pipeline = None
