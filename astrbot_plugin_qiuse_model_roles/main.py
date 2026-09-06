import copy
import json
from pathlib import Path

from astrbot import logger
from astrbot.api import star
from astrbot.core.provider.register import register_provider_adapter
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.api.web import request, json_response


_context = None


@register_provider_adapter(
    "qiuse_openai_fallback", "OpenAI compatible auxiliary model with fallback"
)
class AuxiliaryFallbackProvider(ProviderOpenAIOfficial):
    async def text_chat(self, *args, **kwargs):
        fallback_args = copy.deepcopy(args)
        fallback_kwargs = copy.deepcopy(kwargs)
        try:
            result = await super().text_chat(*args, **kwargs)
            if result.role == "err":
                raise RuntimeError("Auxiliary model returned an error response")
            return result
        except Exception as exc:
            fallback_id = self.provider_config.get("fallback_provider_id")
            fallback = _context.get_provider_by_id(fallback_id) if _context and fallback_id else None
            if fallback is None or fallback is self:
                raise
            logger.warning("Auxiliary model failed (%s); using %s", type(exc).__name__, fallback_id)
            # Preserve the input, but let the fallback select its own model.
            fallback_kwargs.pop("model", None)
            return await fallback.text_chat(*fallback_args, **fallback_kwargs)


class Main(star.Star):
    def __init__(self, context: star.Context, config=None):
        super().__init__(context, config)
        global _context
        _context = context
        context.register_web_api("/astrbot_plugin_qiuse_model_roles/page/config", self.get_config, ["GET"], "Get model role config")
        context.register_web_api("/astrbot_plugin_qiuse_model_roles/page/config", self.save_config, ["POST"], "Save model role config")

    def _path(self):
        return Path("/AstrBot/data/cmd_config.json")

    async def get_config(self):
        data = json.loads(self._path().read_text(encoding="utf-8-sig"))
        providers = {x.get("id"): x.get("model", "") for x in data.get("provider", [])}
        settings = data.get("provider_settings", {})
        return json_response({"providers": providers, "fallback": settings.get("fallback_chat_models", []), "compress": settings.get("llm_compress_provider_id", "")})

    async def save_config(self):
        payload = await request.json(default={})
        data = json.loads(self._path().read_text(encoding="utf-8-sig"))
        values = payload if isinstance(payload, dict) else {}
        allowed = {"deepseek_main", "gemini_aux", "gpt_backup"}
        for item in data.get("provider", []):
            if item.get("id") in allowed and isinstance(values.get(item.get("id")), str):
                item["model"] = values[item["id"]].strip()
        settings = data.setdefault("provider_settings", {})
        if isinstance(values.get("fallback"), list):
            settings["fallback_chat_models"] = [str(x) for x in values["fallback"] if str(x) in {"gemini_aux", "gpt_backup"}]
        if isinstance(values.get("compress"), str):
            settings["llm_compress_provider_id"] = values["compress"]
        self._path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return json_response({"ok": True})
