"""阶段一消息处理管线：归档、去重、权限和基础决策。"""

from dataclasses import dataclass

from .decision import Decision, DecisionEngine
from .message import UnifiedMessage
from .permissions import PermissionService, Role
from .storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class PipelineResult:
    action: Decision
    accepted: bool
    reason: str
    duplicate: bool = False


class MessagePipeline:
    def __init__(self, store: SQLiteStore, permissions: PermissionService, decision: DecisionEngine):
        self.store = store
        self.permissions = permissions
        self.decision = decision

    def handle(self, message: UnifiedMessage, *, auto_reply: bool = True) -> PipelineResult:
        if not self.store.record_message(message):
            return PipelineResult(Decision.IGNORE, False, "duplicate_message", duplicate=True)

        decision = self.decision.decide(message, auto_reply=auto_reply)
        if decision.action == Decision.COMMAND:
            group_id = message.conversation_id if message.is_group else None
            if not self.permissions.allowed(message.sender_id, group_id, Role.GROUP_ADMIN):
                return PipelineResult(Decision.IGNORE, False, "admin_permission_required")
        return PipelineResult(decision.action, True, decision.reason)
