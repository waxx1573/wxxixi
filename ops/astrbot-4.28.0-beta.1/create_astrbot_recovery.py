"""Create a local runtime recovery image without Compose-injected credentials."""
import json
import re
import subprocess
import sys


def restore_environment(container_env, image_env):
    baseline = dict(item.split("=", 1) for item in image_env)
    changes = []
    for item in container_env:
        name, value = item.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            raise ValueError("unsupported environment variable name")
        expected = baseline.get(name, "")
        if value != expected:
            changes.extend(["--change", "ENV " + name + "=" + json.dumps(expected)])
    return changes


def main():
    container = json.loads(subprocess.check_output(["docker", "inspect", "astrbot"]))[0]
    baseline = json.loads(subprocess.check_output(["docker", "image", "inspect", container["Image"]]))[0]
    changes = restore_environment(container["Config"].get("Env") or [], baseline["Config"].get("Env") or [])
    # No pause: dependency files must be stable; data bind mounts are not captured.
    subprocess.run(["docker", "commit", "--pause=false", *changes, "astrbot", sys.argv[1]],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("runtime_recovery_created; injected environment values excluded")


if __name__ == "__main__":
    main()
