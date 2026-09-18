import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "ops"
    / "astrbot-4.28.0-beta.1"
    / "disable_openai_sdk_retries.py"
)
SPEC = importlib.util.spec_from_file_location("disable_openai_sdk_retries", MODULE_PATH)
PATCHER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PATCHER)


class DisableOpenAISdkRetriesTests(unittest.TestCase):
    def test_patches_both_client_constructors_and_is_idempotent(self):
        original = (
            f"{PATCHER.TIMEOUT_NEEDLE}\nazure\n{PATCHER.CLIENT_NEEDLE}"
            f"\nofficial\n{PATCHER.CLIENT_NEEDLE}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "openai_source.py"
            source.write_text(original, encoding="utf-8")

            PATCHER.patch_openai_source(source)
            PATCHER.patch_openai_source(source)

            patched = source.read_text(encoding="utf-8")
            self.assertEqual(patched.count(PATCHER.CLIENT_REPLACEMENT), 2)
            self.assertIn(PATCHER.TIMEOUT_REPLACEMENT, patched)
            self.assertNotIn(PATCHER.CLIENT_NEEDLE, patched)

    def test_rejects_unexpected_source_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "openai_source.py"
            source.write_text(PATCHER.CLIENT_NEEDLE, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                PATCHER.patch_openai_source(source)

    def test_adds_provider_schema_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "default.py"
            source.write_text(PATCHER.SCHEMA_NEEDLE, encoding="utf-8")

            PATCHER.patch_default_config(source)
            PATCHER.patch_default_config(source)

            patched = source.read_text(encoding="utf-8")
            self.assertEqual(patched.count(PATCHER.SCHEMA_REPLACEMENT), 1)


if __name__ == "__main__":
    unittest.main()
