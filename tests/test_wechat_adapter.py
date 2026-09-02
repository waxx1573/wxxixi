import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from mvp_core import MessageType, from_wechat


class WechatAdapterTests(unittest.TestCase):
    def test_group_message_uses_room_id_and_drops_numeric_sender(self):
        msg = SimpleNamespace(local_id=9, type="text", content="@小助手 你好", sender_id=123, sender="成员", create_time=1700000000)
        chat = SimpleNamespace(_wxid="123@chatroom", who="测试群")
        result = from_wechat(msg, chat, bot_name="小助手")
        self.assertEqual(result.conversation_id, "123@chatroom")
        self.assertTrue(result.is_group)
        self.assertIsNone(result.sender_id)
        self.assertTrue(result.mentioned_bot)
        self.assertEqual(result.message_type, MessageType.TEXT)

    def test_private_image_preserves_stable_sender(self):
        created = datetime(2026, 9, 1, tzinfo=timezone.utc)
        msg = SimpleNamespace(local_id="a", type="image", content="", wxid="wxid_friend", sender="朋友", create_time=created)
        chat = SimpleNamespace(_wxid="wxid_friend", who="朋友")
        result = from_wechat(msg, chat)
        self.assertEqual(result.sender_id, "wxid_friend")
        self.assertEqual(result.message_type, MessageType.IMAGE)
        self.assertFalse(result.is_group)


if __name__ == "__main__":
    unittest.main()
