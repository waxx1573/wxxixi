"""Bounded semantic participation decision for SpectreCore group messages."""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict

log = logging.getLogger("astrbot.spectre.semantic_decision")


class SemanticDecision:
    SYSTEM_PROMPT = (
        "You are the reply/skip classifier for a WeChat group bot. "
        "Reply when the current speaker is addressing the bot, continuing the bot's topic, "
        "or clearly needs the bot to participate. Skip member-to-member chat, unrelated statements, "
        "and messages with no bot-facing intent. Output exactly one lowercase word: reply or skip."
    )

    def __init__(self, clock=time.monotonic, capacity=1024):
        self.clock = clock
        self.capacity = capacity
        self._seen = OrderedDict()
        self._last_reply = OrderedDict()

    @staticmethod
    def _text(event):
        getter = getattr(event, "get_message_outline", None)
        if callable(getter):
            return str(getter() or "").strip()
        return str(getattr(event, "message_str", "") or "").strip()

    @staticmethod
    def _ids(event):
        def value(name):
            getter = getattr(event, name, None)
            return str(getter() or "").strip() if callable(getter) else ""
        return value("get_group_id"), value("get_sender_id"), value("get_self_id")

    @staticmethod
    def _origin(event):
        return str(getattr(event, "unified_msg_origin", "") or "").strip()

    @staticmethod
    def _message_id(event):
        obj = getattr(event, "message_obj", None)
        return str(getattr(obj, "message_id", "") or "").strip()

    @staticmethod
    def _components(event):
        obj = getattr(event, "message_obj", None)
        components = getattr(obj, "message", None)
        if components is None:
            getter = getattr(event, "get_messages", None)
            components = getter() if callable(getter) else []
        return list(components or [])

    @staticmethod
    def _raw_bridge(event):
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        bridge = raw.get("wxbridge") if isinstance(raw, dict) else None
        return bridge if isinstance(bridge, dict) else {}

    def is_self_message(self, event):
        _, sender_id, self_id = self._ids(event)
        return bool(sender_id and self_id and sender_id == self_id)

    def _event_key(self, event):
        group_id, sender_id, _ = self._ids(event)
        message_id = self._message_id(event)
        if message_id:
            marker = "id:" + message_id
        else:
            payload = f"{self._text(event)}|{int(self.clock() // 5)}"
            marker = "digest:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
        return self._origin(event), group_id, sender_id, marker

    def accept_event(self, event, ttl=30.0):
        if self.is_self_message(event):
            return False, "self_message"
        now = self.clock()
        while self._seen:
            _, stamp = next(iter(self._seen.items()))
            if now - stamp <= ttl:
                break
            self._seen.popitem(last=False)
        key = self._event_key(event)
        if key in self._seen:
            return False, "duplicate"
        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        return True, "accepted"

    def _chat_key(self, event):
        group_id, _, _ = self._ids(event)
        return self._origin(event), group_id

    def note_reply(self, event):
        key = self._chat_key(event)
        if key[1]:
            self._last_reply[key] = self.clock()
            self._last_reply.move_to_end(key)
            while len(self._last_reply) > self.capacity:
                self._last_reply.popitem(last=False)

    def _in_cooldown(self, event, seconds):
        stamp = self._last_reply.get(self._chat_key(event))
        return stamp is not None and self.clock() - stamp < seconds

    def _signals(self, event, keywords, followup_eligible):
        group_id, sender_id, self_id = self._ids(event)
        text = self._text(event)
        native_at = self._raw_bridge(event).get("mentioned") is True
        replies_to_bot = False
        for component in self._components(event):
            kind = str(getattr(component, "type", "") or component.__class__.__name__).lower()
            if kind in {"at", "mention"}:
                target = str(
                    getattr(component, "qq", None)
                    or getattr(component, "target", None)
                    or getattr(component, "user_id", "")
                ).strip()
                native_at = native_at or bool(self_id and target == self_id)
            elif kind == "reply":
                reply_sender = str(
                    getattr(component, "sender_id", None)
                    or getattr(component, "user_id", "")
                ).strip()
                replies_to_bot = bool(self_id and reply_sender == self_id)
        keyword = any(str(item) and str(item).casefold() in text.casefold() for item in keywords)
        return {
            "group_id": group_id,
            "sender_id": sender_id,
            "native_at": native_at,
            "reply_to_bot": replies_to_bot,
            "keyword": keyword,
            "followup": bool(followup_eligible),
        }

    @staticmethod
    def _history_text(history_messages, current_message_id, limit):
        lines = []
        for message in list(history_messages or []):
            message_id = str(getattr(message, "message_id", "") or "")
            if current_message_id and message_id == current_message_id:
                continue
            sender = getattr(message, "sender", None)
            sender_id = str(getattr(sender, "user_id", "") or "unknown")
            sender_name = str(getattr(sender, "nickname", "") or "unknown")
            content = str(getattr(message, "message_str", "") or "").strip()
            if content:
                lines.append(f"{sender_name}({sender_id}): {content[:240]}")
        return "\n".join(lines[-limit:])[-2400:]

    async def should_reply(self, event, config, context, history_messages, keywords, followup_eligible=False):
        signals = self._signals(event, keywords, followup_eligible)
        for key in ("native_at", "reply_to_bot", "keyword", "followup"):
            if signals[key]:
                log.info(
                    "Spectre semantic decision: reply reason=%s group=%s sender=%s",
                    key, signals["group_id"], signals["sender_id"],
                )
                return True

        settings = config.get("semantic_decision", {})
        if not settings.get("enabled", True):
            log.info("Spectre semantic decision: skip reason=disabled")
            return False

        cooldown = max(0.0, float(settings.get("cooldown_seconds", 8)))
        if cooldown and self._in_cooldown(event, cooldown):
            log.info("Spectre semantic decision: skip reason=cooldown group=%s", signals["group_id"])
            return False

        provider_id = str(settings.get("provider_id", "gemini_aux_source/gemini-3-flash") or "").strip()
        provider = context.get_provider_by_id(provider_id) if provider_id else None
        if provider is None or not hasattr(provider, "text_chat"):
            log.warning("Spectre semantic decision: skip reason=provider_unavailable provider=%s", provider_id)
            return False

        limit = max(1, min(12, int(settings.get("history_limit", 8))))
        history = self._history_text(history_messages, self._message_id(event), limit)
        prompt = (
            f"Current stable sender ID: {signals['sender_id']}\n"
            f"Directly mentioned: {str(signals['native_at']).lower()}\n"
            f"Replies to bot: {str(signals['reply_to_bot']).lower()}\n"
            f"In follow-up window: {str(signals['followup']).lower()}\n"
            f"Recent messages from this group only:\n{history or 'No reliable history'}\n\n"
            f"Current message:\n{self._text(event)[:800]}\n\n"
            "Output reply or skip only."
        )
        timeout = max(1.0, min(30.0, float(settings.get("timeout_seconds", 10))))
        try:
            result = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    image_urls=[],
                    func_tool=None,
                    system_prompt=self.SYSTEM_PROMPT,
                ),
                timeout=timeout,
            )
            if getattr(result, "role", None) == "err":
                raise RuntimeError("provider_error")
            decision = str(getattr(result, "completion_text", "") or "").strip().lower()
            if decision not in {"reply", "skip"}:
                log.warning("Spectre semantic decision: skip reason=invalid_output")
                return False
            log.info(
                "Spectre semantic decision: %s reason=model group=%s sender=%s",
                decision, signals["group_id"], signals["sender_id"],
            )
            return decision == "reply"
        except asyncio.TimeoutError:
            log.warning("Spectre semantic decision: skip reason=timeout provider=%s", provider_id)
            return False
        except Exception as exc:
            log.warning("Spectre semantic decision: skip reason=error type=%s", type(exc).__name__)
            return False


semantic_decision = SemanticDecision()
