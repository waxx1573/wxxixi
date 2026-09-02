import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mvp_core import Decision, StageOneRuntime


class RuntimeTests(unittest.TestCase):
    def test_runtime_persists_and_deduplicates_raw_callback(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = StageOneRuntime(Path(folder), keywords=("机器人",))
            msg = SimpleNamespace(local_id=1, type="text", content="机器人在吗", sender_id="wxid_user", sender="用户")
            chat = SimpleNamespace(_wxid="room@chatroom", who="测试群")
            self.assertEqual(runtime.handle(msg, chat).action, Decision.REPLY)
            duplicate = runtime.handle(msg, chat)
            self.assertTrue(duplicate.duplicate)
            runtime.close()

    def test_self_message_is_blocked_before_archiving(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = StageOneRuntime(Path(folder), keywords=("机器人",))
            msg = SimpleNamespace(
                local_id=7, type="text", content="机器人在吗",
                sender_id="wxid_bot", sender="西西", attr="self",
            )
            chat = SimpleNamespace(_wxid="room@chatroom", who="测试群")
            result = runtime.handle(msg, chat, bot_name="西西")
            self.assertEqual(result.action, Decision.IGNORE)
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "self_message")
            self.assertEqual(runtime._ensure().store.count_messages(), 0)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
