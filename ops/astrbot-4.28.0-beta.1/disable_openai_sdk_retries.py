"""Make OpenAI SDK retries provider-configurable in AstrBot 4.28."""

from pathlib import Path


OPENAI_SOURCE = Path("/AstrBot/astrbot/core/provider/sources/openai_source.py")
DEFAULT_CONFIG = Path("/AstrBot/astrbot/core/config/default.py")
CLIENT_NEEDLE = (
    "                timeout=self.timeout,\n"
    "                http_client=self._create_http_client(provider_config),"
)
CLIENT_REPLACEMENT = (
    "                timeout=self.timeout,\n"
    "                max_retries=self.openai_sdk_max_retries,\n"
    "                http_client=self._create_http_client(provider_config),"
)
TIMEOUT_NEEDLE = (
    "        if isinstance(self.timeout, str):\n"
    "            self.timeout = int(self.timeout)\n\n"
    "        if not isinstance(self.custom_headers, dict) or not self.custom_headers:"
)
TIMEOUT_REPLACEMENT = (
    "        if isinstance(self.timeout, str):\n"
    "            self.timeout = int(self.timeout)\n"
    "        try:\n"
    "            self.openai_sdk_max_retries = max(\n"
    "                0, int(provider_config.get(\"openai_sdk_max_retries\", 2))\n"
    "            )\n"
    "        except (TypeError, ValueError):\n"
    "            self.openai_sdk_max_retries = 2\n\n"
    "        if not isinstance(self.custom_headers, dict) or not self.custom_headers:"
)
SCHEMA_NEEDLE = (
    "                    \"ollama_disable_thinking\": {\n"
    "                        \"description\": \"关闭思考模式\","
)
SCHEMA_REPLACEMENT = (
    "                    \"openai_sdk_max_retries\": {\n"
    "                        \"description\": \"OpenAI SDK 内部重试次数\",\n"
    "                        \"type\": \"int\",\n"
    "                        \"hint\": \"设为 0 时由 AstrBot 立即处理失败并切换备用模型。\",\n"
    "                    },\n"
    + SCHEMA_NEEDLE
)


def patch_openai_source(path: Path = OPENAI_SOURCE) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(CLIENT_REPLACEMENT) == 2 and CLIENT_NEEDLE not in source:
        if TIMEOUT_REPLACEMENT in source and TIMEOUT_NEEDLE not in source:
            return
    if source.count(CLIENT_NEEDLE) != 2 or source.count(TIMEOUT_NEEDLE) != 1:
        raise RuntimeError("unexpected AsyncOpenAI constructor layout")
    source = source.replace(TIMEOUT_NEEDLE, TIMEOUT_REPLACEMENT)
    path.write_text(source.replace(CLIENT_NEEDLE, CLIENT_REPLACEMENT), encoding="utf-8")


def patch_default_config(path: Path = DEFAULT_CONFIG) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(SCHEMA_REPLACEMENT) == 1 and source.count(SCHEMA_NEEDLE) == 1:
        return
    if source.count(SCHEMA_NEEDLE) != 1:
        raise RuntimeError("unexpected provider schema layout")
    path.write_text(source.replace(SCHEMA_NEEDLE, SCHEMA_REPLACEMENT), encoding="utf-8")


if __name__ == "__main__":
    patch_openai_source()
    patch_default_config()
