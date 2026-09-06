import asyncio
from contextlib import nullcontext
import logging
import os
from pathlib import Path
import sys
import subprocess
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
from uia_sender import UiaSender


class TextBridgeTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
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

    def event(self, mode='all', data=None):
        state.group_reply_mode = mode
        with patch.object(bridge_core.threading, 'Timer'), patch.object(bridge_core, 'push_event', return_value=True) as push:
            self.bridge.add_to_buffer(data or self.data)
            for key in list(self.bridge.pending_buffers):
                self.bridge.process_sender(key)
            return push.call_args.args[0] if push.called else None

    def test_all_and_batch_wake_astrbot(self):
        for mode in ('all', 'batch'):
            with self.subTest(mode=mode):
                event = self.event(mode)
                self.assertEqual(event['message'][0], {'type':'at', 'data':{'qq':'123'}})
                self.assertEqual(event['message_type'], 'group')
                self.assertEqual(state._ob_id_to_contact[event['group_id']], 'TestGroup')

    def test_mention_mode_requires_mention(self):
        self.assertIsNone(self.event('mention'))
        event = self.event('mention', dict(self.data, content='@TestBot hello'))
        self.assertEqual(event['message'][0]['type'], 'at')
        self.assertNotIn('@TestBot', event['raw_message'])

    def test_other_groups_blocked_before_media_processing(self):
        for content in ('hello', '[\u56fe\u7247]', '[\u8868\u60c5]'):
            with self.subTest(content=content), patch.object(bridge_core.threading, 'Thread') as thread:
                self.assertIsNone(self.event(data=dict(self.data, groupName='OtherGroup', content=content)))
                thread.assert_not_called()

    def test_private_text_preserved(self):
        event = self.event(data=dict(self.data, sessionId='friend', sessionType='private', groupName=''))
        self.assertEqual(event['message_type'], 'private')
        self.assertEqual(event['message'], [{'type':'text', 'data':{'text':'hello'}}])

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
            asyncio.run(ob_protocol._handle_ob_api(dict(action='send_msg', echo='test', params={
                'group_id':'456', 'message':[{'type':'text','data':{'text':'hello'}}]})))
        state.sender_instance.send_text.assert_called_once_with('TestGroup', 'hello')
        self.assertIsNone(self.event())

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
        sender._window = Mock(NativeWindowHandle=123)
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
        sender._window = Mock(NativeWindowHandle=123)
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
        target.GetSelectionItemPattern.return_value.Select.assert_called_once()
        target.Click.assert_called_once()
        other.GetSelectionItemPattern.assert_not_called()
        self.assertNotIn(call('{Enter}'), sender._auto.SendKeys.call_args_list)

    def test_contact_selection_accepts_latest_message_suffix_in_chat_title(self):
        sender = self.contact_sender()
        target = Mock(AutomationId='session_item_TestGroup')
        sender._window.ListControl.return_value.GetChildren.return_value = [target]
        title = Mock(Name='TestGroup latest message')
        editor = Mock()
        editor.SetFocus.return_value = True
        editor.GetRuntimeId.return_value = [9]
        sender._auto.GetFocusedControl.side_effect = [editor]
        sender._window.Control.side_effect = [title, editor]
        with patch('uia_sender.time.sleep'):
            self.assertTrue(sender._switch_contact('TestGroup'))
        target.Click.assert_called_once()

    def test_missing_or_duplicate_session_never_types(self):
        for items in ([], [Mock(AutomationId='session_item_TestGroup')] * 2):
            sender = self.contact_sender()
            sender._window.ListControl.return_value.GetChildren.return_value = items
            self.assertFalse(sender._switch_contact('TestGroup'))
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


if __name__ == '__main__':
    unittest.main()
