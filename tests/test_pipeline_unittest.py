import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mvp_core import Decision, DecisionEngine, MessagePipeline, MessageType, PermissionService, Role, SQLiteStore, UnifiedMessage


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "bot.db")
        self.permissions = PermissionService(self.store._conn)
        self.pipeline = MessagePipeline(self.store, self.permissions, DecisionEngine(("机器人",)))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def msg(self, mid, content, sender="wxid_member", group=True):
        return UnifiedMessage(
            conversation_id="room@chatroom" if group else "wxid_friend",
            message_id=mid,
            sender_id=sender,
            sender_name="Member",
            message_type=MessageType.TEXT,
            content=content,
            created_at=datetime.now(timezone.utc),
            is_group=group,
        )

    def test_duplicate_is_archived_once(self):
        first = self.pipeline.handle(self.msg("1", "机器人在吗"))
        second = self.pipeline.handle(self.msg("1", "机器人在吗"))
        self.assertEqual(first.action, Decision.REPLY)
        self.assertTrue(second.duplicate)
        self.assertEqual(self.store.count_messages(), 1)

    def test_command_requires_group_admin(self):
        denied = self.pipeline.handle(self.msg("2", "/设置"))
        self.assertEqual(denied.action, Decision.IGNORE)
        self.assertEqual(denied.reason, "admin_permission_required")
        self.permissions.set_role("wxid_member", Role.GROUP_ADMIN, "room@chatroom")
        allowed = self.pipeline.handle(self.msg("3", "/设置"))
        self.assertEqual(allowed.action, Decision.COMMAND)


if __name__ == "__main__":
    unittest.main()
