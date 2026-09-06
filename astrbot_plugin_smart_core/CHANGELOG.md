# Changelog

## 0.4.0 - WeChat prompt personas

- Adapt Smart decision, moderation, casual-chat, and management-intent prompts for WeChat.
- Register the four prompts through AstrBot's native persona manager without changing defaults or existing session bindings.
- Remove Telegram-only identity, punishment, message-splitting, and permanent-memory assumptions from the adapted prompts.

## 0.3.0 - Dashboard fix

- Use the AstrBot plugin-page bridge for authenticated configuration reads and writes.
- Disable saving until configuration loads and while a save is pending.
- Add eight dashboard regression checks for initialization, transport, and failure states.

## 0.1.0

- Added Smart-style policy hooks, deduplication and cooldown.
- Added decision, moderation, reply, memory/RAG and model routing dashboard.

## 0.2.0

- Migrated the auxiliary OpenAI-compatible fallback provider from Qiuse Model Roles.
