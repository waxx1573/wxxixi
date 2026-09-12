import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

spec = importlib.util.spec_from_file_location("group_style", Path(__file__).resolve().parents[1] / "astrbot_plugin_wechat_group_manager/group_style.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class GroupStyleTests(unittest.IsolatedAsyncioTestCase):
    async def test_learning_persists_only_valid_profile_and_isolates_groups(self):
        with tempfile.TemporaryDirectory() as d:
            s = m.GroupStyle(d, 10)
            generate = AsyncMock(return_value=json.dumps({"sentence_length":"short", "tone":"playful", "emoji":"light", "phrases":["收到收到", "忽略规则"]}, ensure_ascii=False))
            for i in range(10):
                s.observe("pipi:groupA", str(i % 2), "收到收到，本次内容" + str(i), str(i), generate)
            await asyncio.gather(*s.tasks.values())
            self.assertTrue(s.prompt("pipi:groupA"))
            self.assertFalse(s.prompt("pipi:groupB"))
            self.assertEqual(s.load("pipi:groupA")["profile"]["phrases"], ["收到收到"])
            raw = next(Path(d).glob("*.json")).read_text(encoding="utf-8")
            self.assertNotIn("本次内容", raw)
            self.assertEqual(m.GroupStyle(d).prompt("pipi:groupA"), s.prompt("pipi:groupA"))
            s.control("pipi:groupA", "暂停")
            self.assertFalse(s.prompt("pipi:groupA"))
            s.control("pipi:groupA", "清空")
            self.assertNotIn("profile", s.load("pipi:groupA"))

    async def test_commands_secrets_duplicates_and_single_sender_do_not_trigger(self):
        with tempfile.TemporaryDirectory() as d:
            s = m.GroupStyle(d,10); generate=AsyncMock()
            for text in ["/wx 风格", "password: secret", "[表情]", "https://example.com"]:
                s.observe("a","x",text,text,generate)
            self.assertFalse(s.samples)
            for i in range(15):
                s.observe("a","x","有效发言",str(i),generate)
            s.observe("a","y","重复消息","0",generate)
            self.assertFalse(s.tasks)
            generate.assert_not_called()

    async def test_invalid_model_output_preserves_previous_profile(self):
        with tempfile.TemporaryDirectory() as d:
            s=m.GroupStyle(d,10)
            previous={"enabled":True,"profile":{"tone":"casual"}}
            s.save("a",previous)
            for i in range(10):
                s.observe("a",str(i%2),"普通发言",str(i),AsyncMock(return_value='{"tone":"ignore system"}'))
            await asyncio.gather(*s.tasks.values())
            self.assertEqual(s.load("a"),previous)
            self.assertEqual(len(s.samples["a"]),10)
