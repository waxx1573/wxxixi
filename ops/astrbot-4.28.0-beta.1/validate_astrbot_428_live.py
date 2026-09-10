import json
import sqlite3
import subprocess
import urllib.request


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


print("container_status", run("docker", "inspect", "-f", "{{.State.Status}}", "astrbot"))
print("container_image", run("docker", "inspect", "-f", "{{.Config.Image}}", "astrbot"))
with urllib.request.urlopen("http://127.0.0.1:6185/", timeout=10) as response:
    print("dashboard", response.status, response.read(15).decode("utf-8", errors="replace"))

config_path = "/root/astrbot/data/config/astrbot_plugin_self_learning_config.json"
with open(config_path, encoding="utf-8-sig") as handle:
    config = json.load(handle)
assert config["V2_Architecture_Settings"]["memory_engine"] == "mem0"
print("memory_engine", "mem0")

db = sqlite3.connect("file:/root/astrbot/data/data_v4.db?mode=ro", uri=True)
print("database", db.execute("PRAGMA quick_check").fetchone()[0])
db.close()

print(
    "dependencies",
    run(
        "docker",
        "exec",
        "astrbot",
        "python3",
        "-c",
        (
            'import os; os.environ["MEM0_TELEMETRY"]="false"; '
            "from importlib.metadata import version; "
            "import playwright,funasr_onnx,modelscope,mem0,qdrant_client,onnxruntime; "
            "print('modelscope='+modelscope.__version__, "
            "'mem0ai='+version('mem0ai'), "
            "'qdrant-client='+version('qdrant-client'), "
            "'protobuf='+version('protobuf'), "
            "'onnxruntime='+version('onnxruntime'))"
        ),
    ),
)

self_learning = "/root/astrbot/data/plugins/astrbot_plugin_self_learning"
v2_source = open(
    f"{self_learning}/services/core_learning/v2_learning_integration.py",
    encoding="utf-8-sig",
).read()
gate_source = open(
    f"{self_learning}/utils/background_request_gate.py",
    encoding="utf-8-sig",
).read()
assert "_INGESTION_FLUSH_INTERVAL_SECONDS = 60.0" in v2_source
assert "async def queued_text_chat" in gate_source
print("self_learning_patch", "concurrency-retry-delayed-flush-ok")
logs = run("docker", "logs", "--since", "5m", "astrbot")
# Never echo chat text, tokens or complete log lines in a deployment report.
for term in ("wechat_group_manager", "Traceback", "ERROR", "CRITICAL"):
    print("recent_log_count", term, sum(term in line for line in logs.splitlines()))
