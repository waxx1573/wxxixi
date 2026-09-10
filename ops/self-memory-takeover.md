# self-learning memory takeover (2026-09-10)

Installed mem0ai 1.0.11 in the AstrBot runtime after isolated compatibility tests. OpenAI SDK 3.6.0 unchanged; protobuf changed 7.36.1 -> 6.33.6 for the dependency constraint; onnxruntime import tested. No separate network database or new credentials.

Rebuilds must install ops/self-memory-requirements.txt in the project image, apply ops/apply_self_memory_storage.py to the deployed self-learning directory, and repeat ops/test_mem0_takeover.py in the container. Runtime pip installation alone does not survive container replacement. Do not apply unreviewed package upgrades.

The complete 4.3.0 concurrency, retry, and low-traffic ingestion fix is stored in `ops/patches/0001-fix-serialize-self-learning-memory-ingestion.patch`. It was generated from local branch `codex/self-learning-concurrency-memory`, commit `c144632eb98675d65abb7ae3699c448c559ec4ba`, based on upstream commit `c58fc1622a73ea91b27a08fc965f2a692bcc4711`. Apply it to a clean compatible self-learning checkout with `git am ops/patches/0001-fix-serialize-self-learning-memory-ingestion.patch`; review conflicts instead of forcing it onto a different upstream layout.

AstrBot plugin configuration: V2_Architecture_Settings.memory_engine=mem0, embedding_provider_id=ollama_embedding_local; knowledge_engine stays legacy. Integration_Settings.delegate_memory_to_livingmemory=false. Existing target groups, style and realtime settings retained.

Mem0 Qdrant and history reside in self-learning data_dir (mem0_qdrant and mem0_history.db). Existing SQLite message/style data preserved, not bulk imported. MEM0_TELEMETRY=false before mem0 import.

Service logs confirmed Memory manager started and embedding qwen3-embedding:0.6b dim=1024. The latest related regression run passed 85 tests. Real WeChat messages in the Record group wrote two facts to Mem0; Qdrant contained two matching points scoped by the stable group ID and sender ID. A single low-traffic fact was stored by the delayed flush without a follow-up trigger message. Legacy memories tables are not the Mem0 vector store; zero legacy rows no longer diagnoses disabled memory.

Rollback: restore memory_engine=legacy and the prior embedding field, or restore `/root/astrbot/backups/self-learning-pre-concurrency-20260910.tar.gz`, then restart only AstrBot; keep stored data unless deletion is separately authorised. Real tests remain restricted to the Record group, ID 5550672362880625580. Use `http://127.0.0.1:6185/` for the management health check; GET on port 6199 returns 405 and is not a readiness check.
