"""不依赖模型的第一阶段回复决策。"""

from dataclasses import dataclass
from enum import Enum

from .message import UnifiedMessage


class Decision(str, Enum):
    REPLY = "reply"
    IGNORE = "ignore"
    MODERATION = "moderation"
    COMMAND = "command"


@dataclass(frozen=True, slots=True)
class DecisionResult:
    action: Decision
    reason: str


class DecisionEngine:
    def __init__(self, keywords: tuple[str, ...] = ()):
        self.keywords = tuple(k for k in keywords if k)

    def decide(self, message: UnifiedMessage, *, auto_reply: bool = True) -> DecisionResult:
        text = message.content.strip()
        if text.startswith("/"):
            return DecisionResult(Decision.COMMAND, "command_prefix")
        if not auto_reply:
            return DecisionResult(Decision.IGNORE, "group_auto_reply_disabled")
        if message.mentioned_bot or message.reply_to_bot:
            return DecisionResult(Decision.REPLY, "directed_at_bot")
        if any(keyword in text for keyword in self.keywords):
            return DecisionResult(Decision.REPLY, "keyword_match")
        if message.is_group:
            return DecisionResult(Decision.IGNORE, "ordinary_group_chat")
        return DecisionResult(Decision.REPLY, "private_chat")
