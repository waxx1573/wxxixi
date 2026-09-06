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

config_path = "/root/astrbot/data/config/astrbot_plugin_wechat_group_manager_config.json"
with open(config_path, encoding="utf-8-sig") as handle:
    config = json.load(handle)
print("admins", config.get("administrator_ids", []))
print("platforms", config.get("platform_names", []))

db = sqlite3.connect("/root/astrbot/data/data_v4.db")
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
        "import playwright,funasr_onnx,modelscope; print(modelscope.__version__)",
    ),
)
logs = run("docker", "logs", "--since", "5m", "astrbot")
for line in logs.splitlines():
    if any(term in line for term in ("wechat_group_manager", "Traceback", "ERROR", "CRITICAL")):
        print("log", line)
