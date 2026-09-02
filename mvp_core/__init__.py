"""微信机器人 MVP 的平台无关核心类型。"""

from .decision import Decision, DecisionEngine
from .message import MessageType, UnifiedMessage
from .permissions import PermissionService, Role
from .pipeline import MessagePipeline, PipelineResult
from .runtime import StageOneRuntime
from .storage import SQLiteStore
from .wechat_adapter import from_wechat

__all__ = [
    "Decision",
    "DecisionEngine",
    "MessageType",
    "PermissionService",
    "MessagePipeline",
    "PipelineResult",
    "StageOneRuntime",
    "Role",
    "SQLiteStore",
    "UnifiedMessage",
    "from_wechat",
]
