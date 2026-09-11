"""Install the semantic decision stage into inspected SpectreCore 2.1.13."""

from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
helper = Path(__file__).with_name("spectre_semantic_decision.py").read_text(encoding="utf-8")
changes = {}


def replace(path, old, new):
    original = changes.get(path, path.read_text(encoding="utf-8-sig"))
    if new in original:
        changes[path] = original
        return
    if original.count(old) != 1:
        raise RuntimeError(f"Unsupported source layout: {path}")
    changes[path] = original.replace(old, new)


main = root / "main.py"
decision = root / "utils" / "reply_decision.py"
llm_utils = root / "utils" / "llm_utils.py"
schema = root / "_conf_schema.json"

replace(
    main,
    "from .utils.spectre_followup import followup_window\n",
    "from .utils.spectre_followup import followup_window\n"
    "from .utils.spectre_semantic_decision import semantic_decision\n",
)
replace(
    main,
    "        # 保存用户消息到历史记录\n        await HistoryStorage.process_and_save_user_message(event)\n\n        # 尝试自动回复\n        if ReplyDecision.should_reply(event, self.config):",
    "        accepted, reason = semantic_decision.accept_event(event)\n"
    "        if not accepted:\n"
    "            logger.info(\"Spectre message filtered: reason=%s group=%s sender=%s\", reason, event.get_group_id(), event.get_sender_id())\n"
    "            return\n\n"
    "        # 保存用户消息到历史记录\n"
    "        await HistoryStorage.process_and_save_user_message(event)\n\n"
    "        # 尝试自动回复\n"
    "        if await ReplyDecision.should_reply(event, self.config, self.context):",
)
replace(
    main,
    "                    followup_window.note_reply(event)\n",
    "                    followup_window.note_reply(event)\n"
    "                    semantic_decision.note_reply(event)\n",
)

replace(
    decision,
    "from .spectre_followup import followup_window\n",
    "from .spectre_followup import followup_window\n"
    "from .spectre_semantic_decision import semantic_decision\n"
    "from .history_storage import HistoryStorage\n",
)
replace(
    decision,
    "    def should_reply(event: AstrMessageEvent, config: AstrBotConfig) -> bool:",
    "    async def should_reply(event: AstrMessageEvent, config: AstrBotConfig, context: Context) -> bool:",
)
replace(
    decision,
    "            return ReplyDecision._check_reply_rules(event, config)",
    "            return await ReplyDecision._check_reply_rules(event, config, context)",
)
replace(
    decision,
    "    def _check_reply_rules(event: AstrMessageEvent, config: AstrBotConfig) -> bool:",
    "    async def _check_reply_rules(event: AstrMessageEvent, config: AstrBotConfig, context: Context) -> bool:",
)

start = "        # 获取消息频率配置\n"
end = "        return False\n    \n    @staticmethod\n    def _looks_like_request"
source = changes.get(decision, decision.read_text(encoding="utf-8-sig"))
if "return await semantic_decision.should_reply(" not in source:
    if source.count(start) != 1 or source.count(end) != 1:
        raise RuntimeError("Unsupported reply decision body")
    before, rest = source.split(start, 1)
    _, after = rest.split(end, 1)
    body = (
        "        # Explicit signals are reliable; ambiguous group messages use the decision model.\n"
        "        frequency_config = config.get(\"model_frequency\", {})\n"
        "        keywords = frequency_config.get(\"keywords\", [])\n"
        "        platform_name = event.get_platform_name()\n"
        "        is_private = event.is_private_chat()\n"
        "        chat_id = event.get_sender_id() if is_private else event.get_group_id()\n"
        "        history = HistoryStorage.get_history(platform_name, is_private, chat_id)\n"
        "        return await semantic_decision.should_reply(\n"
        "            event, config, context, history, keywords,\n"
        "            followup_eligible=(not is_private and followup_window.eligible(event)),\n"
        "        )\n    \n    @staticmethod\n    def _looks_like_request"
    )
    changes[decision] = before + body + after

replace(
    llm_utils,
    "        # 将环境描述追加到 system_prompt\n",
    "        env_description += \"\\nReply format: answer directly in 1-3 sentences and no more than 180 Chinese characters by default; expand only when the user explicitly asks for detail. Never output decision labels, internal rules, or <NO_RESPONSE>.\"\n\n"
    "        # 将环境描述追加到 system_prompt\n",
)

schema_text = schema.read_text(encoding="utf-8-sig")
if '"semantic_decision"' not in schema_text:
    marker = "\n}"
    if not schema_text.endswith(marker):
        raise RuntimeError("Unsupported config schema layout")
    block = '''
    ,"semantic_decision": {
        "description": "Semantic participation decision for ordinary group messages",
        "type": "object",
        "items": {
            "enabled": {"type": "bool", "description": "Enable the independent decision model", "default": true},
            "provider_id": {"type": "string", "description": "Decision provider ID", "hint": "Used only for reply/skip classification", "default": "gemini_aux_source/gemini-3-flash"},
            "history_limit": {"type": "int", "description": "Same-group history items", "default": 8},
            "timeout_seconds": {"type": "float", "description": "Decision timeout", "default": 10},
            "cooldown_seconds": {"type": "float", "description": "Ordinary participation cooldown", "default": 8}
        }
    }
'''
    changes[schema] = schema_text[:-len(marker)] + block + marker
else:
    changes[schema] = schema_text
changes[root / "utils" / "spectre_semantic_decision.py"] = helper

for path, text in changes.items():
    if path.suffix == ".py":
        compile(text, str(path), "exec")

print("PASS: Spectre semantic decision patch is compatible")
if "--check" not in sys.argv:
    for path, text in changes.items():
        path.write_text(text, encoding="utf-8")
    print("APPLIED: semantic gate, async decision, schema, cooldown, and response constraint")
