import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

spec = importlib.util.spec_from_file_location(
    "wechat_media_adapter",
    Path(__file__).resolve().parents[1] / "astrbot_plugin_wechat_group_manager/media_adapter.py",
)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class MediaAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_inline_image_becomes_plugin_readable_path(self):
        image = SimpleNamespace(file="base64://YWJj", url="",
                                convert_to_file_path=AsyncMock(return_value="/tmp/image.png"))
        event = SimpleNamespace(message_obj=SimpleNamespace(raw_message={"wxbridge": {}}),
                                get_messages=lambda: [image])
        self.assertEqual(await adapter.materialize_wechat_images(event), 1)
        self.assertEqual((image.file, image.url), ("/tmp/image.png", "/tmp/image.png"))

    async def test_other_platform_and_remote_images_are_untouched(self):
        image = SimpleNamespace(file="base64://YWJj", convert_to_file_path=AsyncMock())
        event = SimpleNamespace(message_obj=SimpleNamespace(raw_message={}),
                                get_messages=lambda: [image])
        self.assertEqual(await adapter.materialize_wechat_images(event), 0)
        event.message_obj.raw_message = {"wxbridge": {}}
        image.file = "https://example.invalid/image.png"
        self.assertEqual(await adapter.materialize_wechat_images(event), 0)
        image.convert_to_file_path.assert_not_called()
