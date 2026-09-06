import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class FakeOpenAI:
    async def text_chat(self, *args, **kwargs):
        raise NotImplementedError


stubs = {
    'astrbot': types.SimpleNamespace(logger=Mock()),
    'astrbot.api': types.SimpleNamespace(star=types.SimpleNamespace(Star=object, Context=object)),
    'astrbot.api.web': types.SimpleNamespace(
        request=types.SimpleNamespace(json=AsyncMock()),
        json_response=lambda value: value,
    ),
    'astrbot.core.provider.register': types.SimpleNamespace(
        register_provider_adapter=lambda *args: lambda cls: cls),
    'astrbot.core.provider.sources.openai_source': types.SimpleNamespace(ProviderOpenAIOfficial=FakeOpenAI),
}
path = Path(__file__).resolve().parents[2] / 'astrbot_plugin_qiuse_model_roles' / 'main.py'
spec = importlib.util.spec_from_file_location('test_auxiliary_plugin', path)
plugin = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, stubs):
    spec.loader.exec_module(plugin)


class AuxiliaryFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = plugin.AuxiliaryFallbackProvider()
        self.provider.provider_config = {'fallback_provider_id':'gpt_backup'}
        self.fallback = types.SimpleNamespace(text_chat=AsyncMock(return_value='fallback'))
        plugin._context = Mock()
        plugin._context.get_provider_by_id.return_value = self.fallback

    async def test_success_does_not_use_fallback(self):
        result = types.SimpleNamespace(role='assistant')
        with patch.object(FakeOpenAI, 'text_chat', AsyncMock(return_value=result)):
            self.assertIs(await self.provider.text_chat(prompt='hello'), result)
        self.fallback.text_chat.assert_not_called()

    async def test_failure_preserves_image_input_and_removes_primary_model(self):
        with patch.object(FakeOpenAI, 'text_chat', AsyncMock(side_effect=RuntimeError('offline'))):
            result = await self.provider.text_chat(prompt='describe', image_urls=['test-image'], model='gemini')
        self.assertEqual(result, 'fallback')
        self.fallback.text_chat.assert_awaited_once_with(prompt='describe', image_urls=['test-image'])

    async def test_error_response_uses_fallback(self):
        with patch.object(FakeOpenAI, 'text_chat', AsyncMock(return_value=types.SimpleNamespace(role='err'))):
            self.assertEqual(await self.provider.text_chat(prompt='compress'), 'fallback')

    async def test_cancellation_does_not_start_fallback(self):
        with patch.object(FakeOpenAI, 'text_chat', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.provider.text_chat(prompt='compress')
        self.fallback.text_chat.assert_not_called()
