"""Exercise deployment failure paths with fake Docker; never contacts a server."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("switch_astrbot_428_mem0.sh")
OLD = "astrbot-goofish:4.28.0-beta.1-playwright-sensevoice"
NEW = "astrbot-goofish:4.28.0-playwright-sensevoice-mem0"
RECOVERY = "astrbot-goofish:pre-mem0-runtime"

DOCKER = r"""#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$FIXTURE/calls"
case "$1" in
 image)
    case "$*" in *pre-mem0-runtime*) test -f "$FIXTURE/recovery";; *) exit 0;; esac
    ;;
 inspect)
    case "$*" in
      *napcat*) echo 'napcat-original unchanged-start';;
      *Config.Image*State.Running*) printf '%s true\n' "$(cat "$FIXTURE/current")";;
      *Config.Image*) cat "$FIXTURE/current";;
      *) echo 'running=true';;
    esac
    ;;
 commit) touch "$FIXTURE/recovery";;
 run) exit 0;;
 compose)
    desired=$(sed -n 's/^    image: //p' "$ASTRBOT_ROOT/astrbot.yml")
    case "$*" in
      *config*)
        if [ "$SCENARIO" = config_fail ] && [ "$desired" = "$NEW_IMAGE" ]; then exit 9; fi;;
      *'up -d'*)
        if [ "$SCENARIO" = up_fail ] && [ "$desired" = "$NEW_IMAGE" ]; then exit 9; fi
        printf '%s\n' "$desired" > "$FIXTURE/current";;
    esac
    ;;
 exec)
    if [ "$SCENARIO" = dependency_fail ] && [ "$(cat "$FIXTURE/current")" = "$NEW_IMAGE" ]; then exit 9; fi
    ;;
 *) exit 70;;
esac
"""
CURL = r"""#!/bin/sh
if [ "$SCENARIO" = readiness_fail ] && [ "$(cat "$FIXTURE/current")" = "$NEW_IMAGE" ]; then
 echo 503
else
 echo 200
fi
"""
DATE = r"""#!/bin/sh
n=$(cat "$FIXTURE/clock")
n=$((n + 10))
printf '%s\n' "$n" > "$FIXTURE/clock"
echo "$n"
"""

class SwitchRecoveryTests(unittest.TestCase):
    def run_case(self, scenario):
        shell = os.environ.get("TEST_SH") or shutil.which("sh")
        self.assertTrue(shell, "set TEST_SH to the installed sh executable")
        with tempfile.TemporaryDirectory(prefix="mem0-switch-test-") as tmp:
            root = Path(tmp)
            (root / "backups").mkdir()
            (root / "backups/self-learning-pre-concurrency-20260910.tar.gz").touch()
            (root / "validate_astrbot_428_image.py").write_text("pass\n")
            (root / "create_astrbot_recovery.py").touch()
            initial = RECOVERY if scenario == "from_recovery" else OLD
            if scenario == "from_recovery": (root / "recovery").touch()
            original = f"services:\n  astrbot:\n    image: {initial}\n    environment:\n      - DUMMY=unchanged\n"
            (root / "astrbot.yml").write_text(original, newline="\n")
            (root / "current").write_text(initial + "\n", newline="\n")
            (root / "clock").write_text("0\n", newline="\n")
            commands = root / "bin"
            commands.mkdir()
            for name, text in {"docker": DOCKER, "curl": CURL, "date": DATE,
                               "timeout": '#!/bin/sh\nshift\nexec "$@"\n',
                               "python3": '#!/bin/sh\ncase "$1" in *create_astrbot_recovery.py) docker commit --pause=false astrbot "$2";; *) exit 0;; esac\n',
                               "sleep": '#!/bin/sh\nexit 0\n'}.items():
                p = commands / name
                p.write_text(text, newline="\n")
                p.chmod(0o755)
            env = dict(os.environ, FIXTURE=root.as_posix(), ASTRBOT_ROOT=root.as_posix(),
                       SCENARIO=scenario, NEW_IMAGE=NEW)
            # Convert the fixture path inside sh before prepending PATH on Windows.
            env["TEST_BIN"] = commands.as_posix()
            result = subprocess.run([shell, "-c", 'PATH="$(cd "$TEST_BIN" && pwd):$PATH"; export PATH; exec sh "$1"',
                                     "test", SCRIPT.as_posix()], env=env, text=True,
                                    capture_output=True, timeout=20)
            success = scenario in {"success", "from_recovery"}
            expected = NEW if success else RECOVERY
            self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertEqual((root / "current").read_text().strip(), expected, result.stderr)
            self.assertEqual((root / "astrbot.yml").read_text(), original.replace(initial, expected))
            calls = (root / "calls").read_text()
            self.assertIn("--no-deps --pull never astrbot", calls)
            self.assertNotIn("up -d napcat", calls)
            if not success:
                self.assertIn("rollback_ready=", result.stderr)

    def test_success(self): self.run_case("success")
    def test_reuses_running_verified_recovery(self): self.run_case("from_recovery")
    def test_compose_validation_failure_rolls_back(self): self.run_case("config_fail")
    def test_start_failure_rolls_back(self): self.run_case("up_fail")
    def test_readiness_failure_rolls_back(self): self.run_case("readiness_fail")
    def test_dependency_failure_rolls_back(self): self.run_case("dependency_fail")

class RecoveryPrivacyTests(unittest.TestCase):
    def test_runtime_credentials_are_not_copied_into_image_changes(self):
        from create_astrbot_recovery import restore_environment
        changes = restore_environment(
            ["PATH=/usr/bin", "OPENAI_API_KEY=dummy-sensitive-value", "TZ=Asia/Shanghai"],
            ["PATH=/usr/bin", "TZ=UTC"],
        )
        self.assertNotIn("dummy-sensitive-value", " ".join(changes))
        self.assertEqual(changes, ["--change", 'ENV OPENAI_API_KEY=""', "--change", 'ENV TZ="UTC"'])

if __name__ == "__main__":
    unittest.main()
