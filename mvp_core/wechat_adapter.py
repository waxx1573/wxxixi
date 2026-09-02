"""将 wechatbot-new 的 WxMessage/Chat 转成统一 MVP 消息。"""

from datetime import datetime, timezone
from typing import Any

from .message import MessageType, UnifiedMessage


_TYPE_MAP = {
    "text": MessageType.TEXT,
    "image": MessageType.IMAGE,
    "voice": MessageType.VOICE,
    "link": MessageType.LINK,
    "emotion": MessageType.EMOTION,
    "file": MessageType.FILE,
}


def from_wechat(message: Any, chat: Any, *, bot_name: str | None = None) -> UnifiedMessage:
    """转换现有回调对象；不把昵称当成稳定身份。"""
    conversation_id = str(getattr(chat, "_wxid", None) or getattr(chat, "who", "") or "unknown")
    raw_type = str(getattr(message, "type", "other") or "other").lower()
    content = str(getattr(message, "content", "") or "")
    sender_id = getattr(message, "sender_id", None) or getattr(message, "wxid", None)
    if sender_id is not None:
        sender_id = str(sender_id)
        if sender_id.isdigit():
            sender_id = None
    created = getattr(message, "create_time", None)
    if isinstance(created, (int, float)):
        created_at = datetime.fromtimestamp(created, tz=timezone.utc)
    elif isinstance(created, datetime):
        created_at = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)
    bot_marker = f"@{bot_name}" if bot_name else ""
    return UnifiedMessage(
        conversation_id=conversation_id,
        message_id=str(getattr(message, "local_id", "") or ""),
        sender_id=sender_id,
        sender_name=getattr(message, "sender", None),
        message_type=_TYPE_MAP.get(raw_type, MessageType.OTHER),
        content=content,
        created_at=created_at,
        is_group=conversation_id.endswith("@chatroom"),
        mentioned_bot=bool(bot_marker and bot_marker in content),
        reply_to_bot=bool(getattr(message, "reply_to_bot", False)),
        raw={"wechat_type": raw_type},
    )
