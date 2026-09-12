import hashlib
import json
import sqlite3
import time
import copy
import asyncio
import random
import re
import urllib.request
from collections import OrderedDict
from pathlib import Path

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.web import request, json_response
from astrbot.core.provider.register import register_provider_adapter
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial


_context = None
DEFAULT_GROUP_NAMES = {
    "5550672362880625580": "记录",
    "195576620169779950": "明宇三剑客",
    "834337394775417696": "凡人鲜货炭烤 · 烟火①局",
    "8106142036419670726": "凡人鲜货炭烤 · 烟火②局",
}


@register_provider_adapter("smart_openai_fallback", "Smart Core OpenAI provider with fallback")
class SmartFallbackProvider(ProviderOpenAIOfficial):
    async def text_chat(self, *args, **kwargs):
        try:
            result = await super().text_chat(*args, **kwargs)
            if result.role == "err":
                raise RuntimeError("provider returned an error response")
            return result
        except Exception:
            fallback_id = self.provider_config.get("fallback_provider_id")
            fallback = _context.get_provider_by_id(fallback_id) if _context and fallback_id else None
            if fallback is None or fallback is self:
                raise
            logger.warning("Smart Core fallback: using %s", fallback_id)
            fallback_kwargs = copy.deepcopy(kwargs)
            fallback_kwargs.pop("model", None)
            return await fallback.text_chat(*copy.deepcopy(args), **fallback_kwargs)


class Main(star.Star):
    """Smart-style policy layer. AI generation remains AstrBot's responsibility."""

    # AstrBot binds one native Persona to a Bot. Smart's other prompts are
    # internal classifiers and must not be registered as chat Personas.
    CHAT_PERSONA_ID = "pipi"
    LEGACY_PERSONA_IDS = {
        "Smart-WeChat-Casual-v1",
        "Smart-WeChat-Decision-v1",
        "Smart-WeChat-Moderation-v1",
        "Smart-WeChat-ManageIntent-v1",
    }

    def __init__(self, context: star.Context, config=None):
        super().__init__(context, config or {})
        global _context
        _context = context
        self.config = config or {}
        self.cooldown = float(self.config.get("cooldown_seconds", 3))
        self._recent = OrderedDict()
        self._requests = 0
        self._responses = 0
        self._decision_requests = 0
        self._decision_skips = 0
        self._decision_failures = 0
        self._prompt_dir = Path(__file__).parent / "prompts"
        self._casual_prompt = self._read_prompt("casual")
        self._decision_prompt = self._read_prompt("decision")
        self._config_path = Path("/AstrBot/data/astrbot_plugin_smart_core.json")
        self._astrbot_config_path = Path("/AstrBot/data/cmd_config.json")
        self._group_config_path = Path("/AstrBot/data/config/astrbot_plugin_wechat_group_manager_config.json")
        self._group_names_path = Path(__file__).parent / "group_names.json"
        self._group_db_path = Path("/AstrBot/data/plugin_data/astrbot_plugin_wechat_group_manager/group_manager.sqlite3")
        context.register_web_api("/astrbot_plugin_smart_core/page/config", self.get_config, ["GET"], "Get Smart Core config")
        context.register_web_api("/astrbot_plugin_smart_core/page/config", self.save_config, ["POST"], "Save Smart Core config")
        context.register_web_api("/astrbot_plugin_smart_core/page/groups", self.get_groups, ["GET"], "Get available WeChat groups")
        context.register_web_api("/astrbot_plugin_smart_core/page/groups/add", self.add_groups, ["POST"], "Add managed groups")
        context.register_web_api("/astrbot_plugin_smart_core/page/groups/remove", self.remove_group, ["POST"], "Remove managed group")
        context.register_web_api("/astrbot_plugin_smart_core/page/groups/update", self.update_group, ["POST"], "Update managed group")

    async def initialize(self):
        manager = self.context.persona_manager
        personas = await manager.get_all_personas()
        existing = {item.persona_id: item for item in personas}
        if self.CHAT_PERSONA_ID not in existing:
            # Preserve edits made to the old casual Persona during migration.
            prompt = self._persona_prompt("Smart-WeChat-Casual-v1", self._read_prompt("casual"))
            await manager.create_persona(
                persona_id=self.CHAT_PERSONA_ID,
                system_prompt=prompt,
                tools=None,
                skills=None,
            )
            logger.info("Smart Core: registered native chat persona %s", self.CHAT_PERSONA_ID)
        for persona_id in self.LEGACY_PERSONA_IDS:
            if persona_id in existing:
                await manager.delete_persona(persona_id)
                logger.info("Smart Core: removed legacy internal persona %s", persona_id)
        try:
            root = json.loads(self._astrbot_config_path.read_text(encoding="utf-8-sig"))
            root.setdefault("provider_settings", {})["default_personality"] = self.CHAT_PERSONA_ID
            self._astrbot_config_path.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Smart Core default persona update failed: %s", type(exc).__name__)

    def _read_prompt(self, name: str) -> str:
        try:
            return (self._prompt_dir / f"{name}.md").read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Smart Core prompt unavailable: %s (%s)", name, type(exc).__name__)
            return ""

    def _persona_prompt(self, persona_id: str, fallback: str = "") -> str:
        """Read the current native Persona so dashboard edits affect runtime requests."""
        manager = getattr(self.context, "persona_manager", None)
        getter = getattr(manager, "get_persona_v3_by_id", None)
        if callable(getter):
            try:
                persona = getter(persona_id)
                prompt = persona.get("prompt", "") if isinstance(persona, dict) else getattr(persona, "prompt", "")
                if isinstance(prompt, str) and prompt.strip():
                    return prompt.strip()
            except Exception as exc:
                logger.debug("Smart Core native persona unavailable: %s", type(exc).__name__)
        return fallback.strip()

    @staticmethod
    def _message_text(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_message_str", None)
        value = getter() if callable(getter) else getattr(event, "message_str", "")
        return str(value or "").strip()

    @classmethod
    def _is_command_message(cls, event: AstrMessageEvent) -> bool:
        """Let AstrBot command handlers run before Smart policy logic."""
        text = cls._message_text(event)
        if not text:
            return False
        return bool(
            re.search(
                r"(?:^|[\s:：])/(?!/)(?:[^\s]+)|(?:^|[\s:：])wx(?:[\s:：]|$)",
                text,
                flags=re.IGNORECASE,
            )
        )

    def _mentioned(self, event: AstrMessageEvent) -> bool:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        bridge = raw.get("wxbridge") if isinstance(raw, dict) else None
        if isinstance(bridge, dict) and bridge.get("synthetic_wakeup") is True:
            return bridge.get("mentioned") is True
        self_ids = set()
        get_self_id = getattr(event, "get_self_id", None)
        if callable(get_self_id):
            self_id = str(get_self_id() or "").strip()
            if self_id:
                self_ids.add(self_id)
        try:
            data = json.loads(self._group_config_path.read_text(encoding="utf-8-sig"))
            values = data.get("bot_self_ids", []) if isinstance(data, dict) else []
            if isinstance(values, str):
                values = values.replace(";", ",").split(",")
            self_ids.update(str(item).strip() for item in values if str(item).strip())
        except (OSError, json.JSONDecodeError):
            pass
        if not self_ids:
            return False
        get_messages = getattr(event, "get_messages", None)
        for part in get_messages() if callable(get_messages) else []:
            if type(part).__name__.lower() not in {"at", "mention"}:
                continue
            target = str(
                getattr(part, "qq", None)
                or getattr(part, "target", None)
                or getattr(part, "user_id", "")
            ).strip()
            if target in self_ids:
                return True
        return False

    @staticmethod
    def _decision_context(req) -> str:
        lines = []
        for item in list(getattr(req, "contexts", None) or [])[-6:]:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            content = item.get("content", "")
            if not isinstance(content, str):
                continue
            content = content.strip()
            if content:
                lines.append(f"{item['role']}: {content[:600]}")
        return "\n".join(lines)[-3000:]

    async def _should_reply(self, event: AstrMessageEvent, req, settings: dict) -> bool:
        """Run the Smart decision prompt for the current WeChat message."""
        if settings.get("reply_mode", "smart") != "smart":
            return True
        if self._mentioned(event):
            logger.info("Smart Core: direct mention bypassed decision model")
            return True
        try:
            # Decision is an internal classifier prompt, not a user-facing Persona.
            decision_prompt = self._decision_prompt
            if not decision_prompt:
                raise RuntimeError("decision prompt is unavailable")
            provider_id = str(settings.get("decision_provider_id", "")).strip()
            provider = self.context.get_provider_by_id(provider_id) if provider_id else None
            if provider is None:
                provider = await self.context.get_using_provider_async(
                    getattr(event, "unified_msg_origin", None)
                )
            if provider is None:
                raise RuntimeError("no chat provider available")
            text = self._message_text(event)
            context = self._decision_context(req)
            self._decision_requests += 1
            result = await provider.text_chat(
                prompt=(
                    f"近期群聊上下文：\n{context or '无可靠上下文'}\n\n"
                    f"当前微信群消息：\n{text}\n\n只输出 skip 或 casual。"
                ),
                system_prompt=decision_prompt,
            )
            if getattr(result, "role", None) == "err":
                raise RuntimeError("decision provider returned an error response")
            decision = str(getattr(result, "completion_text", "") or "").strip().lower()
            if decision == "casual":
                return True
            self._decision_skips += 1
            logger.info("Smart Core: decision skipped message")
            return False
        except Exception as exc:
            self._decision_failures += 1
            logger.warning("Smart Core decision failed; stopping request: %s", type(exc).__name__)
            return False

    def _settings(self):
        defaults = {
            "enabled": True, "reply_mode": "smart", "reply_probability": 1.0,
            "cooldown_seconds": self.cooldown, "moderation_enabled": True,
            "memory_enabled": True, "rag_enabled": False, "vision_owner": "astrbot",
            "fallback_enabled": True,
        }
        try:
            saved = json.loads(self._config_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict): defaults.update(saved)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return defaults

    async def get_groups(self):
        """Return detected groups from the running bridge, with saved groups as fallback."""
        detected = []
        try:
            # The independent WeFlow bridge owns this endpoint; the removed
            # AstrBot bridge plugin no longer provides a configuration file.
            base_url = "http://127.0.0.1:8766"
            def fetch():
                with urllib.request.urlopen(base_url + "/api/contacts", timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))
            result = await asyncio.to_thread(fetch)
            if isinstance(result, dict) and isinstance(result.get("groups"), list):
                detected = [x for x in result["groups"] if isinstance(x, dict) and x.get("id")]
        except Exception as exc:
            logger.debug("Smart Core group detection unavailable: %s", type(exc).__name__)
        if not detected:
            try:
                saved_names = json.loads(self._group_names_path.read_text(encoding="utf-8-sig"))
                if isinstance(saved_names, dict):
                    merged_names = dict(DEFAULT_GROUP_NAMES)
                    merged_names.update({str(k): str(v) for k, v in saved_names.items() if str(k).strip()})
                    detected = [{"id": str(group_id), "name": str(name)} for group_id, name in merged_names.items() if str(group_id).strip()]
            except (OSError, json.JSONDecodeError):
                pass
        return json_response({"ok": True, "groups": detected})

    def _stored_groups(self):
        data = self._settings()
        groups = [dict(x) for x in data.get("groups", []) if isinstance(x, dict) and x.get("id")]
        try:
            manager = json.loads(self._group_config_path.read_text(encoding="utf-8-sig"))
            persisted = manager.get("smart_groups", []) if isinstance(manager, dict) else []
            if not groups and isinstance(persisted, list):
                groups = [dict(x) for x in persisted if isinstance(x, dict) and x.get("id")]
        except (OSError, json.JSONDecodeError):
            pass
        result = []
        seen = set()
        for item in groups:
            group_id = str(item.get("id", "")).strip()
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            result.append({
                "id": group_id,
                "name": str(item.get("name", "")).strip()[:100],
                "enabled": bool(item.get("enabled", True)),
                "ppbot": bool(item.get("ppbot", True)),
                "reply_mode": item.get("reply_mode") if item.get("reply_mode") in {"mention", "keyword"} else "mention",
                "moderation": item.get("moderation") if item.get("moderation") in {"off", "standard", "strict"} else "standard",
            })
        return result

    def _persist_groups(self, groups):
        clean = []
        seen = set()
        for item in groups:
            if not isinstance(item, dict):
                continue
            group_id = str(item.get("id", "")).strip()
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            clean.append({
                "id": group_id,
                "name": str(item.get("name", "")).strip()[:100],
                "enabled": bool(item.get("enabled", True)),
                "ppbot": bool(item.get("ppbot", True)),
                "reply_mode": item.get("reply_mode") if item.get("reply_mode") in {"mention", "keyword"} else "mention",
                "moderation": item.get("moderation") if item.get("moderation") in {"off", "standard", "strict"} else "standard",
            })
        manager = json.loads(self._group_config_path.read_text(encoding="utf-8-sig"))
        manager["allowed_group_ids"] = [x["id"] for x in clean if x["enabled"]]
        manager["smart_groups"] = clean
        self._group_config_path.write_text(json.dumps(manager, ensure_ascii=False, indent=2), encoding="utf-8")
        names = dict(DEFAULT_GROUP_NAMES)
        try:
            saved = json.loads(self._group_names_path.read_text(encoding="utf-8-sig"))
            if isinstance(saved, dict):
                names.update({str(k): str(v) for k, v in saved.items() if str(v).strip()})
        except (OSError, json.JSONDecodeError):
            pass
        names.update({x["id"]: x["name"] for x in clean if x["name"]})
        self._group_names_path.parent.mkdir(parents=True, exist_ok=True)
        self._group_names_path.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        quiet = [str(x) for x in manager.get("quiet_hours", ["00:00", "00:00"])]
        quiet = (quiet + ["00:00", "00:00"])[:2]
        rules = str(manager.get("rules_text", ""))
        with sqlite3.connect(self._group_db_path) as db:
            keep_ids = [x["id"] for x in clean] or [""]
            placeholders = ",".join("?" for _ in keep_ids)
            for table in ("groups", "keywords", "events", "message_window", "audit", "announcements"):
                column = "target_group_id" if table == "announcements" else "group_id"
                db.execute(f"DELETE FROM {table} WHERE {column} NOT IN ({placeholders})", keep_ids)
            for group in clean:
                run_level = "active" if group["enabled"] and group["ppbot"] else "shadow"
                db.execute("""INSERT INTO groups(group_id,run_level,reply_mode,rules,quiet_start,quiet_end,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET
                    run_level=excluded.run_level, reply_mode=excluded.reply_mode, updated_at=excluded.updated_at""",
                    (group["id"], run_level, group["reply_mode"], rules, quiet[0], quiet[1], time.time()))
        data = self._settings()
        data["groups"] = clean
        self._config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return clean

    async def add_groups(self):
        payload = await request.json(default={})
        items = payload.get("groups", []) if isinstance(payload, dict) else []
        current = self._stored_groups()
        by_id = {x["id"]: x for x in current}
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and str(item.get("id", "")).strip():
                group_id = str(item["id"]).strip()
                by_id.setdefault(group_id, {"id": group_id, "name": str(item.get("name", group_id)), "enabled": True, "ppbot": True, "reply_mode": "mention", "moderation": "standard"})
        return json_response({"ok": True, "groups": self._persist_groups(list(by_id.values()))})

    async def remove_group(self):
        payload = await request.json(default={})
        group_id = str(payload.get("id", "")).strip() if isinstance(payload, dict) else ""
        if not group_id:
            return json_response({"ok": False, "error": "missing group id"})
        try:
            current = [x for x in self._stored_groups() if x["id"] != group_id]
            return json_response({"ok": True, "groups": self._persist_groups(current)})
        except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
            logger.warning("Smart Core remove group failed: %s", type(exc).__name__)
            return json_response({"ok": False, "error": "删除群失败，请检查群配置文件和数据库"})

    async def update_group(self):
        payload = await request.json(default={})
        item = payload.get("group") if isinstance(payload, dict) else None
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            return json_response({"ok": False, "error": "missing group"})
        group_id = str(item["id"]).strip()
        current = self._stored_groups()
        for index, group in enumerate(current):
            if group["id"] == group_id:
                current[index] = {**group, **item, "id": group_id}
                break
        else:
            current.append(item)
        return json_response({"ok": True, "groups": self._persist_groups(current)})

    async def get_config(self):
        data = self._settings()
        groups = [dict(x) for x in data.get("groups", []) if isinstance(x, dict) and x.get("id")]
        allowed_ids = []
        try:
            manager = json.loads(self._group_config_path.read_text(encoding="utf-8-sig"))
            raw_groups = manager.get("smart_groups", [])
            if isinstance(raw_groups, list) and not groups:
                groups = [dict(x) for x in raw_groups if isinstance(x, dict) and x.get("id")]
            allowed_ids = [str(x) for x in manager.get("allowed_group_ids", []) if str(x).strip()]
        except (OSError, json.JSONDecodeError):
            pass
        names = dict(DEFAULT_GROUP_NAMES)
        try:
            saved_names = json.loads(self._group_names_path.read_text(encoding="utf-8-sig"))
            if isinstance(saved_names, dict):
                names.update({str(k): str(v).strip() for k, v in saved_names.items() if str(v).strip()})
        except (OSError, json.JSONDecodeError):
            pass
        try:
            with sqlite3.connect(self._group_db_path) as db:
                states = {row[0]: {"run_level": row[1], "reply_mode": row[2]} for row in db.execute("SELECT group_id, run_level, reply_mode FROM groups")}
            by_id = {str(group["id"]): group for group in groups}
            visible_ids = set(allowed_ids) | set(by_id)
            for group_id in list(states):
                if str(group_id) not in visible_ids:
                    continue
            for group_id in list(visible_ids):
                group_id = str(group_id)
                group = by_id.setdefault(group_id, {"id": group_id, "name": "", "enabled": group_id in allowed_ids, "ppbot": True, "reply_mode": "mention", "moderation": "standard"})
                group["name"] = str(group.get("name") or names.get(group_id, ""))
                state = states.get(group_id)
                if state:
                    group["ppbot"] = state["run_level"] == "active"
                    group["reply_mode"] = state["reply_mode"]
            data["groups"] = list(by_id.values())
        except (OSError, sqlite3.Error):
            data["groups"] = groups or [{"id": x, "name": names.get(x, ""), "enabled": True, "ppbot": True, "reply_mode": "mention", "moderation": "standard"} for x in allowed_ids]
        try:
            root = json.loads(self._astrbot_config_path.read_text(encoding="utf-8-sig"))
            ps = root.get("provider_settings", {})
            data["model_roles"] = {
                "main": ps.get("default_provider_id", ""),
                "fallback": ",".join(ps.get("fallback_chat_models", [])),
                "vision": ps.get("default_image_caption_provider_id", ""),
                "compress": ps.get("llm_compress_provider_id", ""),
            }
            provider_ids = []
            for value in data["model_roles"].values():
                provider_ids.extend(str(value).split(","))
            manager = getattr(self.context, "provider_manager", None)
            if manager is not None:
                inst_map = getattr(manager, "inst_map", {})
                if isinstance(inst_map, dict):
                    provider_ids.extend(str(key) for key in inst_map)
                for attr in ("provider_insts", "providers", "provider_instances"):
                    values = getattr(manager, attr, None)
                    if isinstance(values, dict):
                        provider_ids.extend(str(key) for key in values)
                    elif isinstance(values, (list, tuple, set)):
                        for item in values:
                            config = getattr(item, "provider_config", None)
                            if isinstance(config, dict):
                                provider_ids.append(str(config.get("id", "")))
                            else:
                                item_id = getattr(item, "id", "")
                                if isinstance(item_id, str):
                                    provider_ids.append(item_id)
            data["providers"] = sorted({item.strip() for item in provider_ids if "/" in item and "<" not in item})
        except (OSError, json.JSONDecodeError):
            data["model_roles"] = {}
        return json_response(data)

    async def save_config(self):
        payload = await request.json(default={})
        values = payload if isinstance(payload, dict) else {}
        data = self._settings()
        for key in ("enabled", "moderation_enabled", "memory_enabled", "rag_enabled", "fallback_enabled"):
            if key in values: data[key] = bool(values[key])
        for key in ("reply_mode", "vision_owner"):
            if isinstance(values.get(key), str): data[key] = values[key]
        for key in ("reply_probability", "cooldown_seconds"):
            if key in values:
                try: data[key] = max(0.0, float(values[key]))
                except (TypeError, ValueError): pass
        self._config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        roles = values.get("model_roles")
        if isinstance(roles, dict):
            try:
                root = json.loads(self._astrbot_config_path.read_text(encoding="utf-8-sig"))
                ps = root.setdefault("provider_settings", {})
                for key, target in (("main", "default_provider_id"), ("vision", "default_image_caption_provider_id"), ("compress", "llm_compress_provider_id")):
                    if isinstance(roles.get(key), str) and roles[key].strip(): ps[target] = roles[key].strip()
                if isinstance(roles.get("fallback"), str):
                    ps["fallback_chat_models"] = [x.strip() for x in roles["fallback"].split(",") if x.strip()]
                self._astrbot_config_path.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Smart Core model role update failed: %s", type(exc).__name__)
        groups = values.get("groups")
        if isinstance(groups, list):
            removed_ids = {str(x).strip() for x in (values.get("removed_group_ids") or []) if str(x).strip()}
            existing_items = [dict(x) for x in data.get("groups", []) if isinstance(x, dict) and x.get("id")]
            try:
                persisted_manager = json.loads(self._group_config_path.read_text(encoding="utf-8-sig"))
                persisted_groups = persisted_manager.get("smart_groups", []) if isinstance(persisted_manager, dict) else []
                if not existing_items and isinstance(persisted_groups, list):
                    existing_items = [dict(x) for x in persisted_groups if isinstance(x, dict) and x.get("id")]
            except (OSError, json.JSONDecodeError):
                pass
            clean = []
            seen = set()
            # Submitted rows win; persisted rows fill in rows omitted by a stale page.
            for item in groups + existing_items:
                if not isinstance(item, dict):
                    continue
                group_id = str(item.get("id", "")).strip()
                if not group_id or group_id in seen or group_id in removed_ids:
                    continue
                seen.add(group_id)
                reply_mode = str(item.get("reply_mode", "mention"))
                if reply_mode not in {"mention", "keyword"}:
                    reply_mode = "mention"
                moderation = str(item.get("moderation", "standard"))
                if moderation not in {"off", "standard", "strict"}:
                    moderation = "standard"
                clean.append({"id": group_id, "name": str(item.get("name", "")).strip()[:100], "enabled": bool(item.get("enabled", True)), "ppbot": bool(item.get("ppbot", True)), "reply_mode": reply_mode, "moderation": moderation})
            try:
                manager = json.loads(self._group_config_path.read_text(encoding="utf-8-sig"))
                manager["allowed_group_ids"] = [x["id"] for x in clean if x["enabled"]]
                manager["smart_groups"] = clean
                self._group_config_path.write_text(json.dumps(manager, ensure_ascii=False, indent=2), encoding="utf-8")
                self._group_names_path.parent.mkdir(parents=True, exist_ok=True)
                existing_names = dict(DEFAULT_GROUP_NAMES)
                try:
                    saved_names = json.loads(self._group_names_path.read_text(encoding="utf-8-sig"))
                    if isinstance(saved_names, dict):
                        existing_names.update({str(k): str(v) for k, v in saved_names.items() if str(v).strip()})
                except (OSError, json.JSONDecodeError):
                    pass
                existing_names.update({x["id"]: x["name"] for x in clean if x["name"]})
                self._group_names_path.write_text(json.dumps(existing_names, ensure_ascii=False, indent=2), encoding="utf-8")
                quiet = [str(x) for x in manager.get("quiet_hours", ["00:00", "00:00"])]
                quiet = (quiet + ["00:00", "00:00"])[:2]
                rules = str(manager.get("rules_text", ""))
                with sqlite3.connect(self._group_db_path) as db:
                    placeholders = ",".join("?" for _ in clean) or "?"
                    keep_ids = [x["id"] for x in clean] or [""]
                    for table in ("groups", "keywords", "events", "message_window", "audit", "announcements"):
                        db.execute(f"DELETE FROM {table} WHERE group_id NOT IN ({placeholders})" if table not in {"announcements"} else f"DELETE FROM {table} WHERE target_group_id NOT IN ({placeholders})", keep_ids)
                    for group in clean:
                        run_level = "active" if group["enabled"] and group["ppbot"] else "shadow"
                        db.execute("""INSERT INTO groups(group_id,run_level,reply_mode,rules,quiet_start,quiet_end,updated_at)
                            VALUES(?,?,?,?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET
                            run_level=excluded.run_level, reply_mode=excluded.reply_mode, updated_at=excluded.updated_at""",
                            (group["id"], run_level, group["reply_mode"], rules, quiet[0], quiet[1], time.time()))
                data["groups"] = clean
                # AstrBot may sanitize custom fields in the group-manager config;
                # keep Smart Core's own canonical copy so saved rows survive reloads.
                self._config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
                logger.warning("Smart Core group config update failed: %s", type(exc).__name__)
                return json_response({"ok": False, "error": "群管理配置保存失败"})
        self.cooldown = data["cooldown_seconds"]
        return json_response({"ok": True, **data})

    def _message_key(self, event):
        text = getattr(event, "message_str", "") or ""
        session = str(getattr(event, "unified_msg_origin", "") or "")
        return hashlib.sha256((session + "\0" + text).encode()).hexdigest()

    @staticmethod
    def _event_group_id(event) -> str:
        try:
            return str(event.get_group_id() or "").split("#", 1)[0].strip()
        except Exception:
            return ""

    def _managed_group(self, group_id: str):
        if not group_id:
            return None
        for group in self._stored_groups():
            if str(group.get("id", "")) == group_id:
                return group
        return None

    @filter.on_llm_request()
    async def smart_policy(self, event: AstrMessageEvent, req) -> None:
        settings = self._settings()
        if self._is_command_message(event):
            logger.info("Smart Core: command bypassed smart policy")
            return
        try:
            self.cooldown = max(0.0, float(settings.get("cooldown_seconds", self.cooldown)))
        except (TypeError, ValueError):
            self.cooldown = max(0.0, self.cooldown)
        self._requests += 1
        group_id = self._event_group_id(event)
        if not bool(settings.get("enabled", True)):
            logger.info("Smart Core: globally disabled, request stopped")
            event.stop_event()
            return
        if group_id:
            group = self._managed_group(group_id)
            if group is None or not group.get("enabled", True) or not group.get("ppbot", True):
                logger.info("Smart Core: unmanaged or disabled group stopped (%s)", group_id)
                event.stop_event()
                return
            casual_prompt = self._persona_prompt(self.CHAT_PERSONA_ID, self._casual_prompt)
            if not casual_prompt:
                logger.warning("Smart Core: casual prompt unavailable, request stopped")
                event.stop_event()
                return
        probability = min(1.0, max(0.0, float(settings.get("reply_probability", 1.0))))
        if probability <= 0.0 or (probability < 1.0 and random.random() >= probability):
            logger.info("Smart Core: reply probability skipped request (%.3f)", probability)
            event.stop_event()
            return
        now = time.monotonic()
        key = self._message_key(event)
        previous = self._recent.get(key)
        if previous is not None and now - previous < self.cooldown:
            logger.info("Smart Core: duplicate request suppressed (%s)", key[:8])
            event.stop_event()
            return
        self._recent[key] = now
        self._recent.move_to_end(key)
        while len(self._recent) > 512:
            self._recent.popitem(last=False)

        if group_id and not await self._should_reply(event, req, settings):
            event.stop_event()
            return

        current_prompt = req.system_prompt or ""
        runtime_casual_prompt = self._persona_prompt(self.CHAT_PERSONA_ID, self._casual_prompt)
        casual_prompt = ""
        if group_id and runtime_casual_prompt and runtime_casual_prompt not in current_prompt:
            casual_prompt = runtime_casual_prompt + "\n\n"
        req.system_prompt = (
            casual_prompt
            + ("你是 Smart 风格的群聊助手。群聊回复要简洁自然。\n" if group_id else "")
            + current_prompt
        )

    @filter.on_llm_response()
    async def smart_response(self, event: AstrMessageEvent, response) -> None:
        self._responses += 1

    @filter.command("smart状态")
    async def smart_status(self, event: AstrMessageEvent):
        yield event.plain_result(
            f"Smart Core\n请求: {self._requests}\n响应: {self._responses}\n"
            f"决策: {self._decision_requests}（跳过 {self._decision_skips}，失败 {self._decision_failures}）\n"
            f"去重缓存: {len(self._recent)}\n冷却: {self.cooldown:g}s"
        )
