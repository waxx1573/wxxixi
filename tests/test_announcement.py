import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_wechat_group_manager.announcement import parse_announcement
from astrbot_plugin_wechat_group_manager.core import Store


class AnnouncementParserTests(unittest.TestCase):
    def test_current_and_all_use_allowlist_ids(self):
        allowed = ["100", "200"]
        self.assertEqual(parse_announcement("/wx 公告 当前 今天 18:00 开会", "100", allowed)[0], ["100"])
        self.assertEqual(parse_announcement("/wx 公告 全部 请查看群规", "100", allowed)[0], ["100", "200"])

    def test_explicit_targets_are_deduplicated_and_checked(self):
        self.assertEqual(
            parse_announcement("/wx 公告 200,100,200 内容", "100", ["100", "200"])[0],
            ["200", "100"],
        )
        with self.assertRaisesRegex(ValueError, "不在允许列表"):
            parse_announcement("/wx 公告 300 内容", "100", ["100"])

    def test_names_and_empty_or_long_content_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_announcement("/wx 公告 记录 内容", "100", ["100"])
        with self.assertRaises(ValueError):
            parse_announcement("/wx 公告 当前 ", "100", ["100"])
        with self.assertRaises(ValueError):
            parse_announcement("/wx 公告 当前 12345", "100", ["100"], max_length=4)


class AnnouncementStoreTests(unittest.TestCase):
    def test_cooldown_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "groups.sqlite3")
            self.assertFalse(store.announcement_recent("actor", "100", 30))
            store.record_announcement("actor", "100", "内容", "accepted", "retcode=0")
            self.assertTrue(store.announcement_recent("actor", "100", 30))
            store.close()

    def test_failed_delivery_does_not_start_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "groups.sqlite3")
            store.record_announcement("actor", "100", "内容", "failed", "transport error")
            self.assertFalse(store.announcement_recent("actor", "100", 30))
            store.close()


if __name__ == "__main__":
    unittest.main()
