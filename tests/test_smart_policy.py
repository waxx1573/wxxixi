import asyncio
import importlib
import sys
import types
import unittest
from collections import OrderedDict
from pathlib import Path


def install_astrbot_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    astrbot.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )

    api = types.ModuleType("astrbot.api")
    api.logger = astrbot.logger

    class Star:
        def __init__(self, context=None, config=None):
            self.context = context
            self.config = config or {}

    api.star = types.SimpleNamespace(Star=Star, Context=object)

    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object

    class Filter:
        EventMessageType = types.SimpleNamespace(ALL=object())

        def __getattr__(self, name):
            return lambda *args, **kwargs: (lambda function: function)

    event.filter = Filter()

    platform = types.ModuleType("astrbot.api.platform")
    platform.MessageType = types.SimpleNamespace(GROUP_MESSAGE="group")

    web = types.ModuleType("astrbot.api.web")
    web.request = None
    web.json_response = lambda value: value

    register = types.ModuleType("astrbot.core.provider.register")
    register.register_provider_adapter = lambda *args, **kwargs: (lambda cls: cls)

    openai = types.ModuleType("astrbot.core.provider.sources.openai_source")

    class ProviderOpenAIOfficial:
        pass

    openai.ProviderOpenAIOfficial = ProviderOpenAIOfficial

    paths = types.ModuleType("astrbot.core.utils.astrbot_path")
    paths.get_astrbot_data_path = lambda: str(Path.cwd())

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.platform": platform,
            "astrbot.api.web": web,
            "astrbot.core": types.ModuleType("astrbot.core"),
            "astrbot.core.provider": types.ModuleType("astrbot.core.provider"),
            "astrbot.core.provider.register": register,
            "astrbot.core.provider.sources": types.ModuleType("astrbot.core.provider.sources"),
            "astrbot.core.provider.sources.openai_source": openai,
            "astrbot.core.utils": types.ModuleType("astrbot.core.utils"),
            "astrbot.core.utils.astrbot_path": paths,
        }
    )


install_astrbot_stubs()
smart_module = importlib.import_module("astrbot_plugin_smart_core.main")
group_module = importlib.import_module("astrbot_plugin_wechat_group_manager.main")


class Result:
    def __init__(self, text: str):
        self.completion_text = text


class Provider:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        return Result(self.text)


class Context:
    def __init__(self, provider: Provider):
        self.provider = provider

    def get_provider_by_id(self, provider_id):
        return self.provider if provider_id == "decision" else None

    async def get_using_provider_async(self, unified_msg_origin):
        return self.provider


class Event:
    unified_msg_origin = "wx:group:1"

    def __init__(self, text="请解释一下", messages=None, self_id="bot"):
        self.message_str = text
        self.messages = messages or []
        self.self_id = self_id
        self.stopped = False

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return self.messages

    def get_self_id(self):
        return self.self_id

    def stop_event(self):
        self.stopped = True


class At:
    def __init__(self, target):
        self.target = target


class Request:
    def __init__(self, system_prompt="BASE", contexts=None):
        self.system_prompt = system_prompt
        self.contexts = contexts or []


class SmartDecisionTests(unittest.TestCase):
    def make_main(self, provider_text="casual"):
        provider = Provider(provider_text)
        main = smart_module.Main.__new__(smart_module.Main)
        main.context = Context(provider)
        main._decision_prompt = "DECISION"
        main._group_config_path = Path("missing-smart-test-config.json")
        main._decision_requests = 0
        main._decision_skips = 0
        main._decision_failures = 0
        return main, provider

    def test_decision_uses_recent_context_and_requires_exact_output(self):
        main, provider = self.make_main("casual")
        request = Request(contexts=[{"role": "user", "content": "前一句"}])
        allowed = asyncio.run(main._should_reply(Event(), request, {"reply_mode": "smart"}))
        self.assertTrue(allowed)
        self.assertIn("前一句", provider.calls[0]["prompt"])
        self.assertEqual(provider.calls[0]["system_prompt"], "DECISION")

        provider.text = "casual because it is useful"
        allowed = asyncio.run(main._should_reply(Event(), request, {"reply_mode": "smart"}))
        self.assertFalse(allowed)

    def test_direct_bot_mention_bypasses_decision_provider(self):
        main, provider = self.make_main("skip")
        event = Event(messages=[At("bot")])
        allowed = asyncio.run(main._should_reply(event, Request(), {"reply_mode": "smart"}))
        self.assertTrue(allowed)
        self.assertEqual(provider.calls, [])

    def test_all_mode_bypasses_decision_provider(self):
        main, provider = self.make_main("skip")
        allowed = asyncio.run(main._should_reply(Event(), Request(), {"reply_mode": "all"}))
        self.assertTrue(allowed)
        self.assertEqual(provider.calls, [])

    def test_missing_decision_prompt_fails_closed(self):
        main, provider = self.make_main("casual")
        main._decision_prompt = ""
        allowed = asyncio.run(main._should_reply(Event(), Request(), {"reply_mode": "smart"}))
        self.assertFalse(allowed)
        self.assertEqual(main._decision_failures, 1)
        self.assertEqual(provider.calls, [])

    def test_smart_policy_injects_casual_prompt_once(self):
        main, _ = self.make_main()
        main.cooldown = 0
        main._recent = OrderedDict()
        main._requests = 0
        main._decision_failures = 0
        main._casual_prompt = "CASUAL"
        main._settings = lambda: {
            "enabled": True,
            "reply_probability": 1,
            "cooldown_seconds": 0,
            "reply_mode": "all",
        }
        main._event_group_id = lambda event: "group"
        main._managed_group = lambda group_id: {"enabled": True, "ppbot": True}
        request = Request(system_prompt="CASUAL\n\nBASE")
        asyncio.run(main.smart_policy(Event(), request))
        self.assertEqual(request.system_prompt.count("CASUAL"), 1)
        self.assertIn("BASE", request.system_prompt)


class GroupRoutingTests(unittest.TestCase):
    def test_group_reply_modes(self):
        route = group_module.Main._allow_llm
        self.assertTrue(route("mention", "smart", False))
        self.assertTrue(route("mention", "all", False))
        self.assertFalse(route("mention", "mention", False))
        self.assertTrue(route("mention", "mention", True))
        self.assertFalse(route("keyword", "smart", True))
        self.assertFalse(route("mention", "unknown", False))


if __name__ == "__main__":
    unittest.main()
