import asyncio
import base64
from contextlib import nullcontext
import logging
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, call, mock_open, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Tests must never load credentials, connect to services, or operate WeChat.
sys.modules['config'] = types.SimpleNamespace(
    BOT_NICKNAMES=['TestBot'], BOT_WXID='', ALLOWED_GROUPS=['TestGroup'],
    BUFFER_SECONDS=5, ASTRBOT_ATTACHMENTS='',
)
import config
import state
import bridge_core
import ob_protocol
import main
from uia_sender import UiaSender


class TextBridgeTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        ob_protocol._GROUP_TEXT_COALESCE_SECONDS = 0
        ob_protocol._group_text_pending.clear()
        ob_protocol._group_text_tasks.clear()
        state._self_id_int = 123
        state.group_reply_mode = 'all'
        state._ob_id_to_contact.clear()
        state.bridge_instance = None
        self.bridge = bridge_core.WeFlowBridge(Mock())
        self.data = dict(content='hello', sourceName='Tester', senderName='Tester',
                         sessionId='group@chatroom', sessionType='group', groupName='TestGroup')

    def tearDown(self):
        logging.disable(logging.NOTSET)
        state.bridge_instance = None
        state._ob_ws = None
        ob_protocol._group_text_pending.clear()
        ob_protocol._group_text_tasks.clear()

    def event(self, mode='all', data=None):
        state.group_reply_mode = mode
        with patch.object(bridge_core.threading, 'Timer'), patch.object(bridge_core, 'push_event', return_value=True) as push:
            self.bridge.add_to_buffer(data or self.data)
            for key in list(self.bridge.pending_buffers):
                self.bridge.process_sender(key)
            return push.call_args.args[0] if push.called else None

    def test_message_arriving_during_push_is_scheduled_without_third_message(self):
        events = []
        def push(event):
            events.append(event)
            if len(events) == 1:
                self.bridge.add_to_buffer(dict(self.data, content='follow up'))
            return True
        with patch.object(bridge_core.threading, 'Timer') as timer, \
             patch.object(self.bridge, 'resolve_group_contact', return_value='TestGroup'), \
             patch.object(bridge_core, 'push_event', side_effect=push):
            self.bridge.add_to_buffer(self.data)
            key = next(iter(self.bridge.pending_buffers))
            self.bridge.process_sender(key)
            self.assertEqual(timer.call_count, 2)
            callback = timer.call_args.args[1]
            callback()
            self.assertEqual(len(events), 2)
            self.assertIn('follow up', events[1]['raw_message'])
            self.assertEqual(timer.call_count, 2)

    def test_raw_group_identity_resolves_reply_target(self):
        response = Mock()
        response.json.return_value = {"data": [{"username": "group@chatroom", "nickname": "Actual Group"},
                                                 {"username": "other@chatroom", "nickname": "Other Group"}]}
        with patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True), patch.object(config, 'ACCESS_TOKEN', '', create=True), patch.object(bridge_core.requests, 'get', return_value=response) as get, patch.object(config, 'ALLOWED_GROUPS', ['group@chatroom']):
            event = self.event(data=dict(self.data, groupName='group@chatroom'))
            self.assertEqual(state._ob_id_to_contact[event['group_id']], 'Actual Group')
            self.assertEqual(state._contact_to_session['Actual Group'], 'group@chatroom')
            self.assertEqual(self.bridge.resolve_group_contact('group@chatroom'), 'Actual Group')
            get.assert_called_once()

    def test_missing_group_name_never_uses_member_as_target(self):
        with patch.object(bridge_core.requests, 'get', side_effect=RuntimeError('offline')), patch.object(config, 'ALLOWED_GROUPS', ['group@chatroom']):
            event = self.event(data=dict(self.data, groupName=''))
            self.assertEqual(state._ob_id_to_contact[event['group_id']], 'group@chatroom')

    def test_group_resolution_rejects_other_identity_and_ambiguity(self):
        response = Mock()
        with patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True), patch.object(config, 'ACCESS_TOKEN', '', create=True), patch.object(bridge_core.requests, 'get', return_value=response):
            for rows in ([{"username": "other@chatroom", "nickname": "Wrong"}],
                         [{"username": "group@chatroom", "nickname": "A"}, {"username": "group@chatroom", "nickname": "B"}]):
                response.json.return_value = {"data": rows}
                self.assertEqual(self.bridge.resolve_group_contact('group@chatroom'), 'group@chatroom')

    def test_all_and_batch_forward_without_synthetic_mention(self):
        for mode in ('all', 'batch'):
            with self.subTest(mode=mode):
                event = self.event(mode)
                self.assertFalse(any(part['type'] == 'at' for part in event['message']))
                self.assertEqual(event['message_type'], 'group')
                self.assertEqual(state._ob_id_to_contact[event['group_id']], 'TestGroup')

    def test_mention_mode_requires_mention(self):
        self.assertIsNone(self.event('mention'))
        event = self.event('mention', dict(self.data, content='@TestBot hello'))
        self.assertEqual(event['message'][0]['type'], 'at')
        self.assertNotIn('@TestBot', event['raw_message'])

    def test_only_original_mentions_wake_native_agent(self):
        for mode in ('all', 'batch'):
            plain = self.event(mode)
            self.assertFalse(plain['wxbridge']['synthetic_wakeup'])
            self.assertFalse(plain['wxbridge']['mentioned'])
            mentioned = self.event(mode, dict(self.data, content='@TestBot hello'))
            self.assertTrue(mentioned['wxbridge']['mentioned'])
            self.assertEqual(plain['message'][0]['type'], 'text')
            self.assertEqual(mentioned['message'][0], {'type':'at', 'data':{'qq':'123'}})

    def test_command_and_wake_prefix_are_not_wrapped(self):
        for text in ('/reset', '/wx 状态', 'TestBot search https://example.com'):
            with self.subTest(text=text):
                event = self.event(data=dict(self.data, content=text))
                self.assertEqual(event['message'], [{'type':'text', 'data':{'text':text}}])
                self.assertEqual(event['sender']['nickname'], 'Tester')

    def test_fullwidth_mention_maps_to_onebot_at(self):
        event = self.event(data=dict(self.data, content='＠TestBot hello'))
        self.assertEqual(event['message'][0]['type'], 'at')
        self.assertNotIn('＠TestBot', event['message'][1]['data']['text'])

    def test_other_groups_blocked_before_media_processing(self):
        for content in ('hello', '[\u56fe\u7247]', '[\u8868\u60c5]'):
            with self.subTest(content=content), patch.object(bridge_core.threading, 'Thread') as thread:
                self.assertIsNone(self.event(data=dict(self.data, groupName='OtherGroup', content=content)))
                thread.assert_not_called()

    def test_voice_detection_is_exact_and_accepts_sse_and_rest(self):
        for data in ({"content": "[语音]"}, {"localType": 34}, {"mediaType": "voice"}, {"msgType": "34"}):
            self.assertTrue(bridge_core._is_voice_message(data))
            self.assertFalse(self.bridge.should_ignore(dict(data, sourceName="Tester")))
        self.assertFalse(bridge_core._is_voice_message({"parsedContent": "请解释语音识别"}))

    def test_voice_is_blocked_before_download_in_unapproved_group(self):
        with patch.object(bridge_core.threading, 'Thread') as thread:
            self.bridge.add_to_buffer(dict(self.data, content='[语音]', groupName='Other', sessionId='other@chatroom'))
            thread.assert_not_called()

    def test_voice_processing_preserves_member_identity_and_cleans_temp(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'voice'); audio_path=f.name
        try:
            with patch.object(self.bridge, '_fetch_wechat_audio', return_value=audio_path), patch.object(bridge_core.threading, 'Timer'), patch.object(self.bridge, 'resolve_group_contact', return_value='TestGroup'), patch.object(bridge_core, 'push_event', return_value=True) as push:
                data=dict(self.data, content='[语音]', rawid='voice-1')
                self.bridge.process_voice_message(data)
                self.bridge.process_sender('group@chatroom_Tester')
                event=push.call_args.args[0]
                self.assertEqual(event['user_id'],state._wxid_to_int('group@chatroom_Tester'))
                self.assertEqual(event['message'][0]['type'],'record')
                self.assertEqual(base64.b64decode(event['message'][0]['data']['file'][9:]),b'voice')
                self.assertFalse(os.path.exists(audio_path))
        finally:
            if os.path.exists(audio_path): os.unlink(audio_path)

    def test_voice_download_requires_original_identity(self):
        with patch.object(bridge_core.requests, 'get') as get:
            self.assertIsNone(self.bridge._fetch_wechat_audio('group@chatroom', {}))
            get.assert_not_called()

    def test_voice_message_is_not_ignored_when_content_is_empty(self):
        self.assertFalse(self.bridge.should_ignore({"type": 34, "content": "", "sourceName": "Tester"}))

    def test_audio_segment_is_portable_base64_record(self):
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as handle:
            handle.write(b"voice-bytes")
            path = handle.name
        try:
            segment = bridge_core._audio_segment_from_path(path)
            self.assertEqual(segment["type"], "record")
            self.assertTrue(segment["data"]["file"].startswith("base64://"))
            self.assertEqual(base64.b64decode(segment["data"]["file"][9:]), b"voice-bytes")
        finally:
            os.unlink(path)

    def test_voice_event_keeps_record_segment_when_downloaded(self):
        voice = dict(content="[语音]", type=34, sourceName="Tester", senderName="Tester", sessionId="group@chatroom", sessionType="group", groupName="TestGroup")
        with patch.object(self.bridge, "_fetch_wechat_audio", return_value=None), patch.object(bridge_core.threading, "Thread") as thread, patch.object(bridge_core.threading, "Timer"):
            self.bridge.add_to_buffer(voice)
            thread.assert_called_once()

    def test_private_text_preserved(self):
        event = self.event(data=dict(self.data, sessionId='friend', sessionType='private', groupName=''))
        self.assertEqual(event['message_type'], 'private')
        self.assertEqual(event['message'], [{'type':'text', 'data':{'text':'hello'}}])

    def test_media_download_matches_original_message_not_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            listing = Mock(status_code=200)
            listing.raise_for_status.return_value = None
            listing.json.return_value = {'messages': [
                {'serverId': 'newer', 'createTime': 100, 'localType': 3, 'mediaUrl': '/wrong'},
                {'serverId': 'original', 'createTime': 100, 'localType': 3, 'mediaUrl': '/right'}]}
            media = Mock(status_code=200)
            media.iter_content.return_value = [b'GIF89a-test']
            with (
                patch.object(config, 'ASTRBOT_ATTACHMENTS', directory),
                patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True),
                patch.object(config, 'ACCESS_TOKEN', 'test', create=True),
                patch.object(bridge_core.requests, 'get', side_effect=[listing, media]) as get,
            ):
                path = self.bridge._fetch_wechat_image('group', {'rawid': 'original', 'timestamp': 100})
                self.assertEqual(Path(path).read_bytes(), b'GIF89a-test')
                query = get.call_args_list[0].kwargs['params']
                self.assertEqual({key: query[key] for key in ('media', 'image', 'voice', 'video', 'emoji')},
                                 {'media':'1', 'image':'1', 'voice':'0', 'video':'0', 'emoji':'0'})
                self.assertEqual(get.call_args_list[1].args[0], 'http://example.invalid/right')
                self.assertFalse(get.call_args_list[1].kwargs['allow_redirects'])
            with patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True), patch.object(config, 'ACCESS_TOKEN', 'test', create=True), patch.object(bridge_core.requests, 'get', return_value=listing) as get:
                self.assertIsNone(self.bridge._fetch_wechat_image('group', {'rawid': 'missing'}))
                get.assert_called_once()

    def test_concurrent_media_downloads_keep_distinct_files(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            listing = Mock(status_code=200)
            listing.json.return_value = {'messages': [
                {'serverId': 'original', 'mediaType': 'sticker', 'mediaUrl': '/image'}]}
            listing.raise_for_status.return_value = None

            def response(url, **kwargs):
                if url.endswith('/api/v1/messages'):
                    return listing
                media = Mock(status_code=200)
                media.iter_content.return_value = [b'GIF89a-test']
                return media
            with (
                patch.object(config, 'ASTRBOT_ATTACHMENTS', directory),
                patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True),
                patch.object(config, 'ACCESS_TOKEN', 'test', create=True),
                patch.object(bridge_core.requests, 'get', side_effect=response),
                ThreadPoolExecutor(max_workers=8) as pool,
            ):
                paths = list(pool.map(lambda _: self.bridge._fetch_wechat_image(
                    'group', {'rawid': 'original'}), range(16)))
            self.assertEqual(len(set(paths)), 16)
            self.assertTrue(all(Path(path).read_bytes() == b'GIF89a-test' for path in paths))

    def test_media_download_requires_original_identity_and_unique_match(self):
        with patch.object(bridge_core.requests, 'get') as get:
            self.assertIsNone(self.bridge._fetch_wechat_image('group'))
            get.assert_not_called()

        listing = Mock(status_code=200)
        listing.raise_for_status.return_value = None
        listing.json.return_value = {'messages': [
            {'serverId':'same', 'localType':3, 'mediaUrl':'/one'},
            {'serverId':'same', 'localType':3, 'mediaUrl':'/two'},
        ]}
        with patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True), \
             patch.object(config, 'ACCESS_TOKEN', 'test', create=True), \
             patch.object(bridge_core.requests, 'get', return_value=listing) as get:
            self.assertIsNone(self.bridge._fetch_wechat_image('group', {'rawid':'same'}))
            get.assert_called_once()

    def test_media_download_can_match_local_id_and_create_time(self):
        with tempfile.TemporaryDirectory() as directory:
            listing = Mock(status_code=200)
            listing.raise_for_status.return_value = None
            listing.json.return_value = {'messages': [
                {'localId':1004, 'createTime':99, 'localType':3, 'mediaUrl':'/wrong'},
                {'localId':1005, 'createTime':100, 'localType':3, 'mediaUrl':'/right'},
            ]}
            media = Mock(status_code=200)
            media.iter_content.return_value = [b'\x89PNG\r\n\x1a\nrest']
            with patch.object(config, 'ASTRBOT_ATTACHMENTS', directory), \
                 patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True), \
                 patch.object(config, 'ACCESS_TOKEN', 'test', create=True), \
                 patch.object(bridge_core.requests, 'get', side_effect=[listing, media]):
                path = self.bridge._fetch_wechat_image('group', {'localId':1005, 'createTime':100})
            self.assertEqual(Path(path).suffix, '.png')

    def test_media_download_rejects_external_redirect_large_and_non_image(self):
        cases = [
            ('external', 'https://elsewhere.invalid/image', None),
            ('redirect', '/image', (302, [b'GIF89a-test'])),
            ('large', '/image', (200, [b'G' * (bridge_core._MAX_INLINE_IMAGE_BYTES + 1)])),
            ('not-image', '/image', (200, [b'<html>not image</html>'])),
        ]
        for name, media_url, media_result in cases:
            with self.subTest(name=name):
                listing = Mock(status_code=200)
                listing.raise_for_status.return_value = None
                listing.json.return_value = {'messages': [
                    {'serverId':'original', 'localType':3, 'mediaUrl':media_url}]}
                responses = [listing]
                if media_result:
                    media = Mock(status_code=media_result[0])
                    media.iter_content.return_value = media_result[1]
                    responses.append(media)
                with patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True), \
                     patch.object(config, 'ACCESS_TOKEN', 'test', create=True), \
                     patch.object(bridge_core.requests, 'get', side_effect=responses) as get:
                    self.assertIsNone(self.bridge._fetch_wechat_image('group', {'rawid':'original'}))
                    self.assertEqual(get.call_count, len(responses))

    def test_emoji_forwards_downloaded_image_as_onebot_segment(self):
        payload = b'wechat-image-bytes'
        with tempfile.NamedTemporaryFile(delete=False) as image_file:
            image_file.write(payload)
            image_path = image_file.name
        try:
            with (
                patch.object(bridge_core.threading, 'Timer'),
                patch.object(self.bridge, '_fetch_wechat_image', return_value=image_path),
                patch.object(bridge_core, 'caption_image_via_ollama', return_value=None),
                patch.object(bridge_core, 'push_event', return_value=True) as push,
            ):
                self.bridge.process_emoji_message(dict(self.data, content='[表情]'))
                for key in list(self.bridge.pending_buffers):
                    self.bridge.process_sender(key)
            event = push.call_args.args[0]
            image = next(part for part in event['message'] if part['type'] == 'image')
            encoded = image['data']['file'].removeprefix('base64://')
            self.assertEqual(base64.b64decode(encoded), payload)
            self.assertEqual(event['raw_message'], '[表情]')
        finally:
            os.unlink(image_path)

    def test_distinct_events_have_distinct_safe_integer_ids(self):
        events = [ob_protocol.make_message_event(
            kind, 123, [{'type':'text', 'data':{'text':'same text'}}], group_id=456)
            for kind in ('group', 'private') for _ in range(100)]
        ids = [event['message_id'] for event in events]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(isinstance(value, int) and 0 < value <= 2**52 for value in ids))

    def test_ids_survive_process_restart(self):
        values = [subprocess.check_output(
            [sys.executable, '-c', 'import state; print(state._wxid_to_int("group@chatroom"))'],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, PYTHONHASHSEED=seed), text=True).strip()
            for seed in ('1', '2')]
        self.assertEqual(values[0], values[1])
        self.assertEqual(int(values[0]), state._wxid_to_int('group@chatroom'))

    def test_group_identity_uses_session_not_display_name(self):
        with patch.object(config, 'ALLOWED_GROUPS', ['group@chatroom']):
            first = self.event()
            second = self.event(data=dict(self.data, groupName='RenamedGroup'))
        self.assertEqual(first['group_id'], second['group_id'])

    def test_group_send_msg_and_echo_suppression(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state._ob_id_to_contact[456] = 'TestGroup'
        state.sender_instance = Mock()
        state.sender_instance.send_text.return_value = True
        state.bridge_instance = self.bridge
        with patch.object(ob_protocol, '_verify_text_delivery', return_value=True):
            async def invoke():
                await ob_protocol._handle_ob_api(dict(action='send_msg', echo='test', params={
                    'group_id':'456', 'message':[{'type':'text','data':{'text':'hello'}}]}))
                await ob_protocol._group_text_tasks['TestGroup']
            asyncio.run(invoke())
        state.sender_instance.send_text.assert_called_once_with('TestGroup', 'hello')
        self.assertIsNone(self.event())

    def test_adjacent_text_segments_merge_without_crossing_media(self):
        message = [
            {'type': 'text', 'data': {'text': '第一段'}},
            {'type': 'text', 'data': {'text': '第二段'}},
            {'type': 'image', 'data': {'file': 'x.png'}},
            {'type': 'text', 'data': {'text': '第三段'}},
        ]
        self.assertEqual(ob_protocol._merge_adjacent_text_segments(message), [
            {'type': 'text', 'data': {'text': '第一段第二段'}},
            {'type': 'image', 'data': {'file': 'x.png'}},
            {'type': 'text', 'data': {'text': '第三段'}},
        ])

    def test_wechat_formatter_removes_markdown_decoration(self):
        source = (
            '# Summary\n\n'
            'I **do not** have *independent* awareness.\n'
            '- first\n- second\n'
            '> quoted\n\n'
            '[docs](https://example.com) and `code`\n\n\n'
            '| Name | Value |\n| --- | ---: |\n| A | 1 |'
        )
        self.assertEqual(
            ob_protocol._format_text_for_wechat(source),
            '【Summary】\n\nI do not have independent awareness.\n'
            '• first\n• second\n引用：quoted\n\n'
            'docs（https://example.com） and code\n\nName：A\nValue：1',
        )

    def test_wechat_formatter_cjk_emphasis_and_literal_stars(self):
        cases = {
            '测试盆栽叫**“小青”**，是**每周三**浇水': '测试盆栽叫“小青”，是每周三浇水',
            '测试＊＊小青＊＊，每周三浇水': '测试小青，每周三浇水',
            '**未闭合强调': '未闭合强调',
            '乘方 `x ** 2`': '乘方 x ** 2',
            '[来源](https://example.com/**/doc)': '来源（https://example.com/**/doc）',
            r'保留 \*\*星号\*\*': '保留 **星号**',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(ob_protocol._format_text_for_wechat(source), expected)

    def test_wechat_formatter_preserves_plain_text(self):
        text = '你好！\n普通文本 123，标点保持不变。'
        self.assertEqual(ob_protocol._format_text_for_wechat(text), text)

    def test_wechat_formatter_preserves_code_whitespace(self):
        source = '```text\n  indented  \n\n    tail  \n```'
        self.assertEqual(
            ob_protocol._format_text_for_wechat(source),
            '  indented  \n\n    tail  ',
        )

    def test_wechat_formatter_preserves_blank_after_list(self):
        self.assertEqual(
            ob_protocol._format_text_for_wechat('- first\n\nafter'),
            '• first\n\nafter',
        )

    def test_wechat_formatter_preserves_nested_list_order(self):
        source = '- first\n  - nested\n\n  after nested\n- last'
        rendered = ob_protocol._format_text_for_wechat(source)
        self.assertLess(rendered.index('nested'), rendered.index('after nested'))
        self.assertLess(rendered.index('after nested'), rendered.index('last'))

    def test_wechat_formatter_parses_collected_markdown_once(self):
        fragments = ['**跨段', '强调**', '', '```python', '  a = 1  ', '```']
        rendered = ob_protocol._format_text_for_wechat('\n'.join(fragments))
        self.assertEqual(rendered, '跨段\n强调\n\n  a = 1  ')

    def test_wechat_formatter_preserves_complex_markdown_content(self):
        cases = {
            '**粗体里有 *斜体***': '粗体里有 斜体',
            '~~删除线~~': '删除线',
            '```json {"a": 1}```': 'json {"a": 1}',
            '[示例](https://example.com/a_(b))': '示例（https://example.com/a_(b)）',
            '标题\n===': '【标题】',
            '---': '────────',
            '- [x] 已完成': '• [x] 已完成',
            r'不要转换 \*星号\* 和 snake_case': '不要转换 *星号* 和 snake_case',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(ob_protocol._format_text_for_wechat(source), expected)

    def test_group_send_formats_before_cache_send_and_readback(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state._ob_id_to_contact[456] = 'TestGroup'
        state.sender_instance = Mock()
        state.sender_instance.send_text.return_value = True
        state.bridge_instance = self.bridge
        with patch.object(ob_protocol, '_verify_text_delivery', return_value=True) as verify:
            async def invoke():
                await ob_protocol._handle_ob_api(dict(action='send_msg', echo='test', params={
                    'group_id':'456', 'message':[{'type':'text','data':{'text':'我**没有**自主意识'}}]}))
                await ob_protocol._group_text_tasks['TestGroup']
            asyncio.run(invoke())
        state.sender_instance.send_text.assert_called_once_with('TestGroup', '我没有自主意识')
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[1], '我没有自主意识')
        self.assertIn('我没有自主意识', self.bridge._sent_recently)
        self.assertNotIn('我**没有**自主意识', self.bridge._sent_recently)

    def test_independent_group_text_calls_coalesce_into_one_bubble(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state._ob_id_to_contact[456] = 'TestGroup'
        state.sender_instance = Mock()
        state.sender_instance.send_text.return_value = True
        state.bridge_instance = self.bridge
        with patch.object(ob_protocol, '_verify_text_delivery', return_value=True):
            async def invoke():
                for text in ('第一段', '第二段', '第三段'):
                    await ob_protocol._handle_ob_api(dict(action='send_group_msg', params={
                        'group_id': '456', 'message': [{'type': 'text', 'data': {'text': text}}]}))
                await ob_protocol._group_text_tasks['TestGroup']
            asyncio.run(invoke())
        state.sender_instance.send_text.assert_called_once_with('TestGroup', '第一段\n第二段\n第三段')

    def test_image_sender_false_is_logged_as_failure(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state.sender_instance = Mock()
        state.sender_instance.send_image.return_value = False
        with patch.object(ob_protocol, '_decode_base64_image', return_value='image.png'), patch.object(ob_protocol, 'log') as log:
            asyncio.run(ob_protocol._handle_ob_api(dict(action='send_private_msg', echo=1,
                params={'user_id':123, 'message':[{'type':'image','data':{'file':'base64://test'}}]})))
        self.assertTrue(log.error.called)
        self.assertFalse(any('图片已发送' in str(c) for c in log.info.call_args_list))

    def test_image_exception_does_not_drop_following_text(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state.sender_instance = Mock(last_failure_retryable=False)
        state.sender_instance.send_image.side_effect = RuntimeError('failed')
        with patch.object(ob_protocol, '_decode_base64_image', return_value='image.png'), patch.object(ob_protocol, '_verify_text_delivery', return_value=True):
            asyncio.run(ob_protocol._handle_ob_api(dict(action='send_private_msg', echo=1,
                params={'user_id':123, 'message':[{'type':'image','data':{'file':'base64://test'}}, {'type':'text','data':{'text':'caption'}}]})))
        state.sender_instance.send_text.assert_called_once_with('123','caption')

    def test_missing_image_is_failure_and_success_is_only_submission(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state.sender_instance = Mock()
        with patch.object(ob_protocol, 'log') as log:
            asyncio.run(ob_protocol._handle_ob_api(dict(action='send_private_msg', echo=1,
                params={'user_id':123, 'message':[{'type':'image','data':{}}]})))
            self.assertTrue(log.error.called)
        with patch.object(ob_protocol, '_decode_base64_image', return_value='image.png'), patch.object(ob_protocol, 'log') as log:
            asyncio.run(ob_protocol._handle_ob_api(dict(action='send_private_msg', echo=2,
                params={'user_id':123, 'message':[{'type':'image','data':{'file':'base64://test'}}]})))
            self.assertFalse(any('图片已发送' in str(c) for c in log.info.call_args_list))

    def test_verified_images_are_rejected_before_sending(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state.sender_instance = Mock()
        asyncio.run(ob_protocol._handle_ob_api(dict(action='send_group_msg_verified',echo='media',
            params={'group_id':123,'message':[{'type':'image','data':{'file':'base64://test'}}]})))
        response=__import__('json').loads(state._ob_ws.send.await_args.args[0])
        self.assertEqual(response['status'],'failed')
        state.sender_instance.send_image.assert_not_called()

    def test_distinct_images_are_not_deduplicated_as_placeholder(self):
        state._outbound_dedupe = {}
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state.sender_instance = Mock()
        with patch.object(ob_protocol, '_decode_base64_image', side_effect=['image-a.png', 'image-b.png']):
            async def run():
                for i in (1, 2):
                    await ob_protocol._handle_ob_api(dict(action='send_private_msg', echo=i,
                        params={'user_id': 123, 'message': [{'type': 'image', 'data': {'file': 'base64://' + str(i)}}]}))
            asyncio.run(run())
        self.assertEqual(state.sender_instance.send_image.call_count, 2)

    def test_distinct_text_requests_can_have_identical_bodies(self):
        state._outbound_dedupe = {}
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state.sender_instance = Mock(last_failure_retryable=False)
        with patch.object(ob_protocol, '_verify_text_delivery', return_value=True):
            async def run():
                for i in (1, 2):
                    await ob_protocol._handle_ob_api(dict(action='send_private_msg', echo=i,
                        params={'user_id': 123, 'message': [{'type': 'text', 'data': {'text': 'ack'}}]}))
            asyncio.run(run())
        self.assertEqual(state.sender_instance.send_text.call_count, 2)

    def test_unsent_text_does_not_suppress_a_later_request(self):
        state._outbound_dedupe = {}
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state.sender_instance = Mock(last_failure_retryable=False)
        state.sender_instance.send_text.side_effect = [False, True]
        with patch.object(ob_protocol, '_verify_text_delivery', return_value=True):
            async def run():
                for i in (1, 2):
                    await ob_protocol._handle_ob_api(dict(action='send_private_msg', echo=i,
                        params={'user_id': 123, 'message': [{'type': 'text', 'data': {'text': 'ack'}}]}))
            asyncio.run(run())
        self.assertEqual(state.sender_instance.send_text.call_count, 2)

    def test_verified_group_send_responds_after_delivery_readback(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state._ob_id_to_contact[456] = 'TestGroup'
        state.bridge_instance = self.bridge

        def send_text(contact, text):
            state._ob_ws.send.assert_not_awaited()
            return True

        state.sender_instance = Mock()
        state.sender_instance.send_text.side_effect = send_text
        with patch.object(ob_protocol, '_verify_text_delivery', return_value=True):
            asyncio.run(ob_protocol._handle_ob_api(dict(
                action='send_group_msg_verified', echo='verified',
                params={'group_id': '456', 'message': [{'type': 'text', 'data': {'text': 'notice'}}]},
            )))
        response = __import__('json').loads(state._ob_ws.send.await_args.args[0])
        self.assertEqual(response['retcode'], 0)
        self.assertEqual(response['data'], {'delivery': 'verified'})

    def test_verified_group_send_reports_delivery_failure(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state._ob_id_to_contact[456] = 'TestGroup'
        state.sender_instance = Mock()
        state.sender_instance.send_text.return_value = False
        asyncio.run(ob_protocol._handle_ob_api(dict(
            action='send_group_msg_verified', echo='failed',
            params={'group_id': '456', 'message': [{'type': 'text', 'data': {'text': 'notice'}}]},
        )))
        response = __import__('json').loads(state._ob_ws.send.await_args.args[0])
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['retcode'], 1200)
        self.assertEqual(response['data']['delivery'], 'failed')

    def test_verified_group_send_reports_sender_exception(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state._ob_id_to_contact[456] = 'TestGroup'
        state.sender_instance = Mock()
        state.sender_instance.send_text.side_effect = RuntimeError('UI unavailable')
        asyncio.run(ob_protocol._handle_ob_api(dict(
            action='send_group_msg_verified', echo='failed',
            params={'group_id': '456', 'message': [{'type': 'text', 'data': {'text': 'notice'}}]},
        )))
        response = __import__('json').loads(state._ob_ws.send.await_args.args[0])
        self.assertEqual(response['retcode'], 1200)
        self.assertIn('RuntimeError', response['data']['error'])

    def test_pre_input_interruption_retries_once(self):
        state._ob_ws = types.SimpleNamespace(send=AsyncMock())
        state._ob_id_to_contact[456] = 'TestGroup'
        sender = Mock()
        attempts = 0

        def send_text(contact, text):
            nonlocal attempts
            attempts += 1
            sender.last_failure_retryable = attempts == 1
            return attempts == 2

        sender.send_text.side_effect = send_text
        state.sender_instance = sender
        sender.wait_until_user_idle.return_value = True
        with patch.object(ob_protocol, '_verify_text_delivery', return_value=True):
            asyncio.run(ob_protocol._handle_ob_api(dict(
                action='send_group_msg_verified', echo='retried',
                params={'group_id': '456', 'message': [{'type': 'text', 'data': {'text': 'notice'}}]},
            )))
        self.assertEqual(sender.send_text.call_count, 2)
        sender.wait_until_user_idle.assert_called_once_with(5.0, 90.0)
        response = __import__('json').loads(state._ob_ws.send.await_args.args[0])
        self.assertEqual(response['retcode'], 0)

    def test_failed_contact_switch_never_pastes_or_caches(self):
        sender = UiaSender.__new__(UiaSender)
        sender._lock = __import__('threading').Lock()
        sender._automation_session = nullcontext
        sender._ready = True
        sender._ensure_window = Mock(return_value=True)
        sender._activate = Mock(return_value=True)
        sender._auto = Mock()
        sender._switch_contact = Mock(return_value=False)
        sender._last_contact = ''
        sender.search_enabled = True
        self.assertFalse(sender.send_text('TestGroup', 'hello'))
        self.assertTrue(sender.last_failure_retryable)
        self.assertEqual(sender._last_contact, '')
        sender._auto.SendKeys.assert_not_called()

    def test_failed_activation_never_searches_or_types(self):
        sender = UiaSender.__new__(UiaSender)
        sender._lock = __import__('threading').Lock()
        sender._automation_session = nullcontext
        sender._ready = True
        sender._ensure_window = Mock(return_value=True)
        sender._activate = Mock(return_value=False)
        sender._switch_contact = Mock()
        sender._auto = Mock()
        self.assertFalse(sender.send_text('TestGroup', 'hello'))
        sender._switch_contact.assert_not_called()
        sender._auto.SendKeys.assert_not_called()

    def test_focus_loss_blocks_each_keyboard_operation(self):
        sender = UiaSender.__new__(UiaSender)
        sender._window = types.SimpleNamespace(NativeWindowHandle=123)
        sender._auto = Mock()
        sender._auto.GetForegroundWindow.return_value = 456
        with self.assertRaises(RuntimeError):
            sender._send_keys('{Ctrl}v')
        sender._auto.SendKeys.assert_not_called()

    def test_activation_checks_foreground_after_switch(self):
        sender = UiaSender.__new__(UiaSender)
        sender._window = Mock(NativeWindowHandle=123, ClassName='mmui::MainWindow')
        sender._window.SetActive.return_value = False
        sender._auto = Mock()
        sender._auto.GetForegroundWindow.side_effect = [456, 123]
        with patch('uia_sender.time.sleep'):
            self.assertTrue(sender._activate())
        sender._auto.SwitchToThisWindow.assert_called_once_with(123)
        sender._auto.SendKeys.assert_not_called()

    def test_activation_replaces_stale_editor_window_handle(self):
        sender = UiaSender.__new__(UiaSender)
        sender._active_hwnd = 456
        sender._window = Mock(NativeWindowHandle=123)
        sender._auto = Mock()
        sender._auto.GetForegroundWindow.return_value = 123
        self.assertTrue(sender._activate())
        self.assertEqual(sender._active_hwnd, 123)
        sender._auto.ShowWindow.assert_called_once_with(123, sender._auto.SW.Restore)
        sender._auto.BringWindowToTop.assert_called_once_with(123)
        sender._auto.SetForegroundWindow.assert_called_once_with(123)

    def contact_sender(self):
        sender = UiaSender.__new__(UiaSender)
        sender._ensure_window = Mock(return_value=True)
        sender._activate = Mock(return_value=True)
        sender._window = Mock(NativeWindowHandle=123, ClassName='mmui::MainWindow')
        sender._auto = Mock()
        sender._auto.GetForegroundWindow.return_value = 123
        sender._window.ListControl.return_value.Exists.return_value = True
        return sender

    def test_contact_selection_uses_exact_session_not_search(self):
        sender = self.contact_sender()
        other = Mock(AutomationId='session_item_TestGroupOther')
        target = Mock(AutomationId='session_item_TestGroup')
        sender._window.ListControl.return_value.GetChildren.return_value = [other, target]
        sender._auto.GetFocusedControl.return_value = target
        title = Mock(Name='TestGroup')
        editor = Mock()
        editor.Exists.return_value = True
        editor.SetFocus.return_value = True
        editor.GetRuntimeId.return_value = [9]
        sender._auto.GetFocusedControl.side_effect = [editor, editor]
        sender._window.Control.side_effect = [title, editor]
        with patch('uia_sender.time.sleep'):
            self.assertTrue(sender._switch_contact('TestGroup'))
        target.GetSelectionItemPattern.assert_not_called()
        target.Click.assert_not_called()
        other.GetSelectionItemPattern.assert_not_called()
        self.assertNotIn(call('{Enter}'), sender._auto.SendKeys.call_args_list)

    def test_current_main_chat_does_not_click_session_again(self):
        sender = self.contact_sender()
        target = Mock(AutomationId='session_item_TestGroup')
        sender._window.ListControl.return_value.GetChildren.return_value = [target]
        title = Mock(Name='TestGroup')
        editor = Mock()
        editor.SetFocus.return_value = True
        editor.GetRuntimeId.return_value = [9]
        sender._auto.GetFocusedControl.side_effect = [editor]
        sender._window.Control.side_effect = [title, editor]
        with patch('uia_sender.time.sleep'):
            self.assertTrue(sender._switch_contact('TestGroup'))
        target.Click.assert_not_called()

    def test_missing_or_duplicate_session_never_types(self):
        for items in ([], [Mock(AutomationId='session_item_TestGroup')] * 2):
            sender = self.contact_sender()
            sender._window.ListControl.return_value.GetChildren.return_value = items
            self.assertFalse(sender._switch_contact('TestGroup'))
            sender._auto.SendKeys.assert_not_called()

    def test_independent_chat_window_requires_same_process_and_exact_name(self):
        sender = self.contact_sender()
        sender._window.ProcessId = 42
        good = Mock(ClassName='mmui::ChatSingleWindow', ProcessId=42)
        good.Name = 'TestGroup'
        other_account = Mock(ClassName='mmui::ChatSingleWindow', ProcessId=43)
        other_account.Name = 'TestGroup'
        other_group = Mock(ClassName='mmui::ChatSingleWindow', ProcessId=42)
        other_group.Name = 'TestGroup Other'
        sender._auto.GetRootControl.return_value.GetChildren.return_value = [
            good, other_account, other_group,
        ]
        self.assertEqual(sender._matching_chat_windows('TestGroup'), [good])

    def test_missing_main_pane_clicks_once_without_opening_child(self):
        sender = self.contact_sender()
        item = Mock(AutomationId='session_item_TestGroup')
        sender._window.ListControl.return_value.GetChildren.return_value = [item]
        title, editor = Mock(Name='TestGroup'), Mock()
        editor.GetRuntimeId.return_value = [9]
        sender._auto.GetFocusedControl.return_value = editor
        sender._named_control = Mock(side_effect=[None, None, title, editor])
        with patch('uia_sender.time.sleep'):
            self.assertTrue(sender._switch_contact('TestGroup'))
        item.Click.assert_called_once()
        sender._auto.SendKeys.assert_not_called()
        self.assertEqual(sender._window.ClassName, 'mmui::MainWindow')

    def test_missing_main_input_stops_without_enter(self):
        sender = self.contact_sender()
        item = Mock(AutomationId='session_item_TestGroup')
        sender._window.ListControl.return_value.GetChildren.return_value = [item]
        sender._named_control = Mock(return_value=None)
        with patch('uia_sender.time.sleep'):
            self.assertFalse(sender._switch_contact('TestGroup'))
        item.Click.assert_called_once()
        sender._auto.SendKeys.assert_not_called()

    def test_named_control_does_not_cross_into_another_window(self):
        sender = self.contact_sender()
        sender._window.Control.return_value.Exists.return_value = False
        sender._window.GetChildren.return_value = []
        self.assertIsNone(sender._named_control('chat_input_field'))
        sender._auto.GetRootControl.assert_not_called()

    def test_independent_chat_window_rejects_mismatched_session_id(self):
        sender = self.contact_sender()
        sender._window.ProcessId = 42
        sender._session_lookup = lambda contact: 'group@chatroom'
        wrong = Mock(ClassName='mmui::ChatSingleWindow', ProcessId=42,
                     AutomationId='ChatSingleWindowother@chatroom')
        wrong.Name = 'TestGroup'
        sender._auto.GetRootControl.return_value.GetChildren.return_value = [wrong]
        with self.assertRaises(RuntimeError):
            sender._matching_chat_windows('TestGroup')

    def test_target_change_blocks_keyboard_input(self):
        sender = self.contact_sender()
        sender._target_contact = 'TestGroup'
        wrong_title = Mock()
        wrong_title.Name = 'AnotherGroup'
        with patch.object(sender, '_named_control', return_value=wrong_title):
            with self.assertRaises(RuntimeError):
                sender._send_keys('{Ctrl}v')
        sender._auto.SendKeys.assert_not_called()

    def test_duplicate_independent_chat_windows_never_type(self):
        sender = self.contact_sender()
        sender._window.ProcessId = 42
        item = Mock(AutomationId='session_item_TestGroup')
        sender._window.ListControl.return_value.GetChildren.return_value = [item]
        chats = [Mock(ClassName='mmui::ChatSingleWindow', ProcessId=42) for _ in range(2)]
        for chat in chats:
            chat.Name = 'TestGroup'
        sender._auto.GetRootControl.return_value.GetChildren.return_value = chats
        self.assertFalse(sender._switch_contact('TestGroup'))
        item.Click.assert_called_once()
        sender._auto.GetRootControl.assert_not_called()
        sender._auto.SendKeys.assert_not_called()

    def test_wrong_focus_in_wechat_blocks_paste(self):
        sender = self.contact_sender()
        editor = Mock()
        editor.GetRuntimeId.return_value = [1, 2]
        sender._auto.GetFocusedControl.return_value.GetRuntimeId.return_value = [1, 3]
        with self.assertRaises(RuntimeError):
            sender._send_keys('{Ctrl}v', editor)
        sender._auto.SendKeys.assert_not_called()

    def test_window_discovery_rejects_wechat_search_window(self):
        sender = self.contact_sender()
        search = Mock(ClassName='SearchWindow')
        search.Name = 'WeChat'
        main = Mock(ClassName='mmui::MainWindow')
        main.Name = 'WeChat'
        sender._auto.GetRootControl.return_value.GetChildren.return_value = [search, main]
        sender._find_window()
        self.assertIs(sender._window, main)

    def test_contact_selection_requires_chat_title_and_editor(self):
        sender = self.contact_sender()
        target = Mock(AutomationId='session_item_TestGroup')
        sender._window.ListControl.return_value.GetChildren.return_value = [target]
        sender._auto.GetFocusedControl.return_value = target
        title = Mock(Name='TestGroup')
        editor = Mock()
        editor.Exists.return_value = True
        editor.SetFocus.return_value = True
        editor.GetRuntimeId.return_value = [9]
        sender._auto.GetFocusedControl.return_value.GetRuntimeId.return_value = [9]
        sender._window.Control.side_effect = [title, editor]
        with patch('uia_sender.time.sleep'):
            self.assertTrue(sender._switch_contact('TestGroup'))
        editor.SetFocus.assert_called_once()

    def test_delivery_requires_outgoing_recent_exact_text(self):
        state._contact_to_session['TestGroup'] = 'group@chatroom'
        with patch.object(config, 'WE_FLOW_BASE_URL', 'http://example.invalid', create=True), \
             patch.object(config, 'ACCESS_TOKEN', 'test', create=True), \
             patch.object(ob_protocol.requests, 'get') as get, \
             patch.object(ob_protocol.time, 'sleep'):
            get.return_value.json.return_value = {'messages':[
                {'isSend':0, 'createTime':100, 'content':'hello'},
                {'isSend':1, 'createTime':10, 'content':'hello'}]}
            self.assertFalse(ob_protocol._verify_text_delivery('TestGroup', 'hello', 100))
            get.return_value.json.return_value['messages'].append(
                {'isSend':1, 'createTime':100, 'content':'hello'})
            self.assertTrue(ob_protocol._verify_text_delivery('TestGroup', 'hello', 100))

    def test_image_caption_falls_back_after_http_error(self):
        config.IMAGE_CAPTION_ENABLED = True
        state.image_caption_provider = 'openai'
        state.image_caption_api_base = 'http://example.invalid/v1'
        state.image_caption_api_key = 'primary-test'
        state.image_caption_model = 'gemini-test'
        state.image_caption_prompt = 'describe'
        state.image_caption_fallbacks = [{'api_base':'http://example.invalid/v1',
                                         'api_key':'fallback-test', 'model':'gpt-test'}]
        failed, passed = Mock(), Mock()
        failed.raise_for_status.side_effect = bridge_core.requests.HTTPError()
        passed.json.return_value = {'choices':[{'message':{'content':'red square'}}]}
        with patch('builtins.open', mock_open(read_data=b'image')), \
             patch.object(bridge_core.requests, 'post', side_effect=[failed, passed]) as post:
            self.assertEqual(bridge_core.caption_image_via_ollama('test.jpg'), 'red square')
        self.assertEqual([call.kwargs['json']['model'] for call in post.call_args_list],
                         ['gemini-test', 'gpt-test'])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        state.running = False
        state.ob_client_started = False
        state.ob_client_thread = None
        state.bridge_thread = None
        state._ob_ws = None
        state._ob_ws_loop = None
        state._ob_ws_ready.clear()

    def tearDown(self):
        logging.disable(logging.NOTSET)
        state.running = False
        state.ob_client_started = False
        state.ob_client_thread = None
        state.bridge_thread = None
        state._ob_ws = None
        state._ob_ws_loop = None
        state._ob_ws_ready.clear()

    def test_start_rejects_live_previous_client(self):
        previous = Mock()
        previous.is_alive.return_value = True
        state.ob_client_thread = previous
        with patch.object(main, 'create_sender') as create_sender, \
             patch.object(main.threading, 'Thread') as thread:
            self.assertFalse(main._start_bridge())
        self.assertFalse(state.running)
        create_sender.assert_not_called()
        thread.assert_not_called()

    def test_stop_waits_for_client_before_clearing_reference(self):
        client = Mock()
        client.is_alive.side_effect = [True, False]
        state.running = True
        state.ob_client_started = True
        state.ob_client_thread = client
        main._stop_bridge()
        client.join.assert_called_once_with(timeout=6)
        self.assertIsNone(state.ob_client_thread)
        self.assertFalse(state.ob_client_started)


if __name__ == '__main__':
    unittest.main()
