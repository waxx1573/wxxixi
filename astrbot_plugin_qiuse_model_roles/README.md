# Qiuse Model Roles

为 AstrBot 的辅助模型调用提供故障转移能力。它注册一个 OpenAI 兼容的 provider 适配器：主 provider 请求失败或返回错误结果时，自动调用配置的备用 provider。

## 适用范围

- Vision 图片描述
- 上下文压缩
- 决策、意图判断和其他轻量辅助调用
- 任何明确选择 `qiuse_openai_fallback` provider 的任务

主回复模型是否使用备用模型，由 AstrBot 全局 `provider_settings.fallback_chat_models` 决定；本插件不替换主模型，也不负责微信桥接。

## 配置方式

在 AstrBot 的 `data/cmd_config.json` 中配置两个 provider：

```json
{
  "id": "gemini_aux",
  "type": "qiuse_openai_fallback",
  "provider_source_id": "gemini_aux_source",
  "model": "gemini-3-flash",
  "fallback_provider_id": "gpt_backup"
}
```

并在全局设置中指定辅助模型：

```json
{
  "default_image_caption_provider_id": "gemini_aux",
  "llm_compress_provider_id": "gemini_aux"
}
```

备用 provider 使用自己的 API 地址、令牌和模型配置。令牌只应保存在本机 AstrBot 运行配置中，不要写入 README、Git 或聊天记录。

## 回退规则

1. 主 provider 返回正常结果时，不调用备用 provider。
2. 主 provider 抛出请求异常或返回错误结果时，调用 `fallback_provider_id`。
3. 备用调用会复用原始文本和图片输入，但让备用 provider 使用自己的模型配置。
4. 备用 provider 也失败时，将错误交回 AstrBot。

## 日志

发生回退时会记录：

```text
Auxiliary model failed (...); using gpt_backup
```

日志只记录 provider ID 和错误类型，不记录 API 令牌。
