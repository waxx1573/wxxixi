# self-learning memory takeover (2026-09-10)

Installed mem0ai 1.0.11 in the AstrBot runtime after isolated compatibility tests. OpenAI SDK 3.6.0 unchanged; protobuf changed 7.36.1 -> 6.33.6 for the dependency constraint; onnxruntime import tested. No separate network database or new credentials.

Rebuilds must install ops/self-memory-requirements.txt in the project image, apply ops/apply_self_memory_storage.py to the deployed self-learning directory, and repeat ops/test_mem0_takeover.py in the container. Runtime pip installation alone does not survive container replacement. Do not apply unreviewed package upgrades.

AstrBot plugin configuration: V2_Architecture_Settings.memory_engine=mem0, embedding_provider_id=ollama_embedding_local; knowledge_engine stays legacy. Integration_Settings.delegate_memory_to_livingmemory=false. Existing target groups, style and realtime settings retained.

Mem0 Qdrant and history reside in self-learning data_dir (mem0_qdrant and mem0_history.db). Existing SQLite message/style data preserved, not bulk imported. MEM0_TELEMETRY=false before mem0 import.

Service logs confirmed Memory manager started and embedding qwen3-embedding:0.6b dim=1024. Synthetic adapter extraction/write/recall/isolation/reopen/delete checks passed. They do not establish factual extraction accuracy, live correction, or WeChat end-to-end success. Legacy memories table is not the mem0 vector store; zero legacy rows no longer diagnoses disabled memory.

Rollback: restore memory_engine=legacy and prior embedding field, restart only AstrBot; keep stored data. No automatic rollback or deletion. Current live validation blocked by local WeFlow/bridge offline; launch attempt rejected by Windows execution policy. Do not bypass policy. Real tests exclusively in Record group, ID 5550672362880625580.
