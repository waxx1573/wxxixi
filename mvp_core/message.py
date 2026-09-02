"""统一微信消息模型，不暴露任何 Telegram 类型。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    LINK = "link"
    EMOTION = "emotion"
    FILE = "file"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class UnifiedMessage:
    conversation_id: str
    message_id: str
    sender_id: str | None
    sender_name: str | None
    message_type: MessageType
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_group: bool = False
    mentioned_bot: bool = False
    reply_to_bot: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def dedup_key(self) -> str:
        """底层 ID 优先；无 ID 时使用稳定内容指纹由存储层生成。"""
        return f"{self.conversation_id}:{self.message_id}"

    def iso_created_at(self) -> str:
        value = self.created_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
