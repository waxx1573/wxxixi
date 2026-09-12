import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_wechat_group_manager.core import Engine, Store


class ModerationEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_blocked_word_has_traceable_rule(self):
        result = Engine(self.store, {"blocked_words": ["诈骗"]}).evaluate("g", "u", "这是诈骗消息")
        self.assertEqual(result.action, "review")
        self.assertEqual(result.category, "blocked_word")
        self.assertEqual(result.rule_id, "blocked_word:诈骗")
        self.assertEqual(result.confidence, 1.0)

    def test_regex_rule_supports_explicit_id(self):
        result = Engine(self.store, {"blocked_regexes": [{"id": "phone", "pattern": r"1[3-9]\d{9}"}]}).evaluate("g", "u", "联系 13812345678")
        self.assertEqual(result.action, "review")
        self.assertEqual(result.rule_id, "phone")

    def test_invalid_regex_is_ignored(self):
        result = Engine(self.store, {"blocked_regexes": ["["]}).evaluate("g", "u", "普通消息")
        self.assertEqual(result.action, "none")

    def test_ad_domain_has_rule_id(self):
        result = Engine(self.store, {"ad_domains": ["example.com"]}).evaluate("g", "u", "访问 https://www.example.com/a")
        self.assertEqual(result.action, "review")
        self.assertEqual(result.rule_id, "advertising_domain")


if __name__ == "__main__":
    unittest.main()
