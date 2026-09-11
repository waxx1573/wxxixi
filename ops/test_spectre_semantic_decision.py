import asyncio
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spectre_semantic_decision import SemanticDecision


class Provider:
    def __init__(self, output="skip", delay=0):
        self.output, self.delay, self.calls = output, delay, []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return SimpleNamespace(completion_text=self.output, role="assistant")


class Context:
    def __init__(self, provider): self.provider = provider
    def get_provider_by_id(self, provider_id):
        return self.provider if provider_id == "decision" else None


class Part:
    def __init__(self, kind, **kwargs):
        self.type = kind
        for key, value in kwargs.items(): setattr(self, key, value)


class Event:
    unified_msg_origin = "wx:group:one"

    def __init__(self, text="ordinary", sender="alice", group="one", message_id="1", parts=None, mentioned=False):
        self.text, self.sender, self.group = text, sender, group
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            message=parts or [],
            raw_message={"wxbridge": {"mentioned": mentioned}},
        )

    def get_message_outline(self): return self.text
    def get_group_id(self): return self.group
    def get_sender_id(self): return self.sender
    def get_self_id(self): return "bot"


def config(timeout=1):
    return {"semantic_decision": {
        "enabled": True,
        "provider_id": "decision",
        "history_limit": 6,
        "timeout_seconds": timeout,
        "cooldown_seconds": 8,
    }}


async def decide(decision, event, provider, keywords=None, followup=False, history=None, timeout=1):
    return await decision.should_reply(
        event, config(timeout), Context(provider), history or [], keywords or [], followup
    )


class SemanticDecisionTests(unittest.TestCase):
    def test_self_and_duplicate_are_filtered(self):
        decision = SemanticDecision()
        self.assertEqual(decision.accept_event(Event(sender="bot"))[1], "self_message")
        event = Event()
        self.assertTrue(decision.accept_event(event)[0])
        self.assertEqual(decision.accept_event(event)[1], "duplicate")

    def test_explicit_signals_bypass_provider(self):
        cases = (
            (Event(mentioned=True), [], False),
            (Event(parts=[Part("reply", sender_id="bot")]), [], False),
            (Event(text="pipi look"), ["pipi"], False),
            (Event(text="continue", message_id="4"), [], True),
        )
        for event, keywords, followup in cases:
            provider = Provider("skip")
            self.assertTrue(asyncio.run(decide(SemanticDecision(), event, provider, keywords, followup)))
            self.assertEqual(provider.calls, [])

    def test_model_reply_gets_same_group_history(self):
        provider = Provider("reply")
        history = [
            SimpleNamespace(message_id="old", sender=SimpleNamespace(user_id="alice", nickname="A"), message_str="previous"),
            SimpleNamespace(message_id="current", sender=SimpleNamespace(user_id="alice", nickname="A"), message_str="current-copy"),
        ]
        event = Event(text="and this", message_id="current")
        self.assertTrue(asyncio.run(decide(SemanticDecision(), event, provider, history=history)))
        prompt = provider.calls[0]["prompt"]
        self.assertIn("previous", prompt)
        self.assertNotIn("current-copy", prompt)
        self.assertIn("alice", prompt)

    def test_skip_invalid_timeout_and_missing_provider_fail_closed(self):
        self.assertFalse(asyncio.run(decide(SemanticDecision(), Event(), Provider("skip"))))
        self.assertFalse(asyncio.run(decide(SemanticDecision(), Event(), Provider("reply because"))))
        self.assertFalse(asyncio.run(decide(SemanticDecision(), Event(), Provider("reply", delay=1.2), timeout=1)))
        result = asyncio.run(SemanticDecision().should_reply(Event(), config(), Context(None), [], []))
        self.assertFalse(result)

    def test_cooldown_is_group_scoped_and_explicit_bypasses(self):
        clock = [100.0]
        decision = SemanticDecision(clock=lambda: clock[0])
        decision.note_reply(Event())
        provider = Provider("reply")
        self.assertFalse(asyncio.run(decide(decision, Event(message_id="2"), provider)))
        self.assertTrue(asyncio.run(decide(decision, Event(group="two", message_id="3"), provider)))
        self.assertTrue(asyncio.run(decide(decision, Event(message_id="4", mentioned=True), provider)))


if __name__ == "__main__":
    unittest.main()
