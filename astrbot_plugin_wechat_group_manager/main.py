from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from pathlib import Path

from astrbot.api import star
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.platform import MessageType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core import Decision, Engine, RUN_LEVELS, Store
from .media_adapter import materialize_wechat_images
from .group_style import GroupStyle


class Main(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        root = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_wechat_group_manager"
        self.store = Store(root / "group_manager.sqlite3")
        self.engine = Engine(self.store, self.config)
        self.group_style = GroupStyle(root / "group_styles", self.config.get("style_batch_size", 30))
        self._smart_config_path = Path(get_astrbot_data_path()) / "astrbot_plugin_smart_core.json"
        self._config_path = (
            Path(get_astrbot_data_path())
            / "config"
            / "astrbot_plugin_wechat_group_manager_config.json"
        )

    def _set(self, key: str) -> set[str]:
        value = self.config.get(key, [])
        if isinstance(value, str):
            value = value.replace(";", ",").split(",")
        return {str(item).strip() for item in value if str(item).strip()}

    @staticmethod
    def _gid(event: AstrMessageEvent) -> str:
        return str(event.get_group_id() or "").split("#", 1)[0]

    @staticmethod
    def _sid(event: AstrMessageEvent) -> str:
        return str(event.get_sender_id() or "").strip()

    @staticmethod
    def _text(event: AstrMessageEvent) -> str:
        function = getattr(event, "get_message_str", None)
        return str(function() if callable(function) else getattr(event, "message_str", "") or "").strip()

    @staticmethod
    def _is_command(event: AstrMessageEvent, text: str) -> bool:
        text = Main._command_text(text)
        if text == "/wx" or text.startswith("/wx "):
            return True
        return text == "wx" or text.startswith("wx ")

    @staticmethod
    def _command_text(text: str) -> str:
        text = text.strip()
        marker = text.find("/wx")
        if marker >= 0:
            return text[marker:].strip()
        return text

    @staticmethod
    def _gname(event: AstrMessageEvent) -> str:
        function = getattr(event, "get_group_name", None)
        if callable(function):
            value = function()
            if value:
                return str(value)
        message = getattr(event, "message_obj", None)
        direct = getattr(message, "group_name", "")
        if direct:
            return str(direct)
        return str(getattr(getattr(message, "group", None), "group_name", "") or "")

    def _platform_allowed(self, event: AstrMessageEvent) -> bool:
        configured = {item.lower() for item in self._set("platform_names")}
        names = {str(event.get_platform_name() or "").lower()}
        get_platform_id = getattr(event, "get_platform_id", None)
        if callable(get_platform_id):
            names.add(str(get_platform_id() or "").lower())
        if configured & names:
            return True
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        return isinstance(raw, Mapping) and isinstance(raw.get("wxbridge"), Mapping)

    def _allowed(self, event: AstrMessageEvent) -> tuple[bool, bool]:
        stable = bool(self._gid(event) and self._gid(event) in self._set("allowed_group_ids"))
        return stable or self._gname(event) in self._set("bootstrap_group_names"), stable

    def _self(self, event: AstrMessageEvent) -> bool:
        sender = self._sid(event)
        get_self_id = getattr(event, "get_self_id", None)
        if sender in self._set("bot_self_ids") or (callable(get_self_id) and sender == str(get_self_id() or "")):
            return True
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        return isinstance(raw, Mapping) and bool((raw.get("wxbridge") or {}).get("is_self"))

    def _is_admin(self, event: AstrMessageEvent, group: str) -> bool:
        """Use the identity assigned by AstrBot, never a second allowlist."""
        try:
            return event.is_admin() is True
        except Exception:
            return False

    def _mentioned(self, event: AstrMessageEvent) -> bool:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        bridge = raw.get("wxbridge") if isinstance(raw, Mapping) else None
        if isinstance(bridge, Mapping) and bridge.get("synthetic_wakeup") is True:
            return bridge.get("mentioned") is True
        for part in event.get_messages():
            if type(part).__name__.lower() in {"at", "mention"}:
                target = str(getattr(part, "qq", None) or getattr(part, "target", None) or getattr(part, "user_id", ""))
                return not self._set("bot_self_ids") or target in self._set("bot_self_ids")
        return False

    @staticmethod
    def _allow_llm(state_reply_mode: str, smart_mode: str, mentioned: bool) -> bool:
        if state_reply_mode == "keyword":
            return False
        if smart_mode == "mention":
            return mentioned
        if smart_mode in {"smart", "all"}:
            return True
        return mentioned

    def _smart_settings(self) -> dict:
        try:
            value = json.loads(self._smart_config_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _smart_group(self, group_id: str) -> dict:
        settings = self._smart_settings()
        for item in settings.get("groups", []):
            if isinstance(item, dict) and str(item.get("id", "")).strip() == str(group_id):
                return item
        return {}

    def _state(self, group: str) -> dict:
        level = str(self.config.get("default_run_level", "capture"))
        if level not in RUN_LEVELS:
            level = "capture"
        return self.store.ensure(group, level, str(self.config.get("reply_mode", "mention")), str(self.config.get("rules_text", "")), [str(item) for item in self.config.get("quiet_hours", ["23:00", "08:00"])])

    @staticmethod
    def _quiet(state: dict) -> bool:
        now, start, end = datetime.now().strftime("%H:%M"), state["quiet_start"], state["quiet_end"]
        if start == end:
            return False
        return start <= now < end if start < end else now >= start or now < end

    async def _command(self, event: AstrMessageEvent, state: dict, text: str):
        group, actor, parts = self._gid(event), self._sid(event), text.split()
        if not self._is_admin(event, group):
            self.store.audit(group, actor, "command", "denied", text)
            yield event.plain_result("无权限：管理员必须按稳定微信发送者 ID 授权。")
            return
        command = parts[1] if len(parts) > 1 else "状态"
        try:
            if command == "风格":
                key = str(event.get_platform_id()) + ":" + group
                action = parts[2] if len(parts) > 2 else "查看"
                if action in {"开启", "暂停", "清空"}:
                    self.group_style.control(key, action)
                elif action != "查看":
                    raise ValueError("用法：/wx 风格 查看、开启、暂停或清空")
                yield event.plain_result(self.group_style.status(key))
            elif command == "状态":
                yield event.plain_result(f"级别={state['run_level']}；模式={state['reply_mode']}；静默={state['quiet_start']}-{state['quiet_end']}；待处理={len(self.store.pending(group))}；群ID={group}")
            elif command in {"暂停", "关闭"}:
                self.store.set_state(group, "run_level", "capture")
                yield event.plain_result("已进入 capture，只采集和审计。")
            elif command in {"开启", "级别"}:
                level = "active" if command == "开启" else parts[2].lower()
                if level in {"assisted", "active"} and group not in self._set("allowed_group_ids"):
                    raise ValueError("assisted/active 要求稳定群 ID 白名单")
                self.store.set_state(group, "run_level", level)
                yield event.plain_result(f"级别已切换为 {level}")
            elif command == "模式":
                self.store.set_state(group, "reply_mode", parts[2].lower())
                yield event.plain_result(f"模式已切换为 {parts[2].lower()}")
            elif command == "静默":
                self.store.set_quiet(group, parts[2], parts[3])
                yield event.plain_result(f"静默时间已设置为 {parts[2]}-{parts[3]}")
            elif command == "群规":
                yield event.plain_result(state["rules"] or "暂未设置群规。")
            elif command == "设置群规":
                self.store.set_rules(group, text.split(None, 2)[2])
                yield event.plain_result("群规已更新。")
            elif command == "关键词" and parts[2] == "添加":
                self.store.add_keyword(group, parts[3], text.split(None, 4)[4])
                yield event.plain_result(f"关键词已添加：{parts[3]}")
            elif command == "关键词" and parts[2] == "删除":
                yield event.plain_result(f"关键词删除={self.store.delete_keyword(group, parts[3])}")
            elif command == "待处理":
                rows = self.store.pending(group)
                yield event.plain_result("无待处理事件。" if not rows else "\n".join(f"#{row['id']} {row['category']} sender={row['sender_id']} {row['reason']}" for row in rows))
            elif command in {"确认", "忽略", "误报"}:
                status = {"确认": "confirmed", "忽略": "ignored", "误报": "false_positive"}[command]
                yield event.plain_result(f"事件更新={self.store.resolve(group, int(parts[2].lstrip('#')), status)}")
            else:
                yield event.plain_result("命令：/wx 状态|暂停|开启|级别 capture|shadow|assisted|active|模式 mention|keyword|静默 HH:MM HH:MM|群规|设置群规|关键词 添加/删除|待处理|确认/忽略/误报")
            self.store.audit(group, actor, command, "success", text)
        except (ValueError, IndexError) as exc:
            self.store.audit(group, actor, command, "failed", str(exc))
            yield event.plain_result(f"命令失败：{exc}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def on_group_message(self, event: AstrMessageEvent):
        probe_text = self._text(event)
        if "/wx" in probe_text or probe_text.strip().lstrip("/").startswith("wx"):
            get_platform_id = getattr(event, "get_platform_id", None)
            logger.info(
                "WXGM command probe: type=%s platform=%s platform_id=%s group=%s sender=%s text=%r",
                event.get_message_type(),
                event.get_platform_name(),
                get_platform_id() if callable(get_platform_id) else "",
                event.get_group_id(),
                event.get_sender_id(),
                probe_text,
            )
        if not self._gid(event) or not self._platform_allowed(event):
            return
        group = self._gid(event) or f"bootstrap:{self._gname(event)}"
        sender, text, state = self._sid(event), self._text(event), self._state(group)
        if self._self(event):
            self.store.audit(group, sender, "self", "ignored")
            event.stop_event()
            return
        # Management commands must remain available for bootstrap and status
        # checks even before a group has been added to the managed allowlist.
        if self._is_command(event, text):
            async for reply in self._command(event, state, self._command_text(text)):
                yield reply
            event.stop_event()
            return
        allowed, stable = self._allowed(event)
        smart_settings = self._smart_settings()
        smart_group = self._smart_group(group)
        if not bool(smart_settings.get("enabled", True)):
            event.stop_event()
            return
        if smart_group and (not bool(smart_group.get("enabled", True)) or not bool(smart_group.get("ppbot", True))):
            event.stop_event()
            return
        if not allowed:
            if self.config.get("block_unmanaged_wechat_groups", True):
                event.stop_event()
            return
        moderation_enabled = bool(smart_settings.get("moderation_enabled", True))
        try:
            resolved = await materialize_wechat_images(event)
            if resolved:
                logger.info("WXGM materialized %s bridge images for plugins", resolved)
        except Exception as exc:
            logger.warning("WXGM bridge image conversion failed: %s", type(exc).__name__)
        moderation_level = str(smart_group.get("moderation", "standard")) if smart_group else "standard"
        decision = self.engine.evaluate(group, sender, text, moderation_enabled=moderation_enabled and moderation_level != "off")
        if decision.action == "review":
            event_id = self.store.add_event(group, sender, decision.category, decision.reason, text)
            self.store.audit(group, sender, "moderation", "queued", f"event={event_id}; {decision.reason}")
        if state["run_level"] in {"capture", "shadow"} or not stable or self._quiet(state) or decision.action == "review":
            event.stop_event()
            return
        if decision.action == "keyword":
            yield event.plain_result(decision.reply)
            event.stop_event()
            return
        if self.config.get("style_learning_enabled", False):
            key = str(event.get_platform_id()) + ":" + group
            provider_id = str(self.config.get("style_provider_id", "")).strip()
            if provider_id:
                async def generate(prompt):
                    result = await self.context.llm_generate(
                        chat_provider_id=provider_id, prompt=prompt,
                        system_prompt="仅提炼聊天表达风格。输出要求的 JSON，不调用工具。",
                    )
                    return result.completion_text
                self.group_style.observe(key, sender, text,
                    getattr(event.message_obj, "message_id", None), generate)
        smart_mode = str(smart_settings.get("reply_mode", "smart"))
        if self._allow_llm(state["reply_mode"], smart_mode, self._mentioned(event)):
            return
        event.stop_event()

    @filter.on_llm_request()
    async def inject_group_style(self, event: AstrMessageEvent, req):
        if not self.config.get("style_learning_enabled", False) or not self._platform_allowed(event):
            return
        group = self._gid(event)
        if not group or group not in self._set("allowed_group_ids"):
            return
        profile = self.group_style.prompt(str(event.get_platform_id()) + ":" + group)
        if profile and "[本群表达风格]" not in (req.system_prompt or ""):
            req.system_prompt = (req.system_prompt or "") + profile

    async def terminate(self):
        await self.group_style.close()
