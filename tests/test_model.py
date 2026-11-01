import os
import unittest
from unittest.mock import patch
from orderbot.model import ChatSession, FakeBackend, OpenAIBackend, ModelError

class ChatTests(unittest.TestCase):
    def test_no_initial_request_and_blank(self):
        class Forbidden:
            def reply(self, messages):
                raise AssertionError('不应请求')
        session = ChatSession(Forbidden())
        self.assertEqual(session.messages, [])
        with self.assertRaises(ValueError):
            session.send('  ')

    def test_fake_without_network(self):
        with patch('socket.socket', side_effect=AssertionError('禁止联网')):
            session = ChatSession(FakeBackend())
            self.assertIn('假回复', session.send('你好'))
            self.assertEqual(len(session.messages), 2)
            session.reset()
            self.assertEqual(session.messages, [])

    def test_failure_keeps_history(self):
        class Broken:
            def reply(self, messages):
                raise ModelError('模拟失败')
        session = ChatSession(Broken())
        with self.assertRaises(ModelError):
            session.send('你好')
        self.assertEqual(session.messages, [])

    def test_real_missing_config_no_request(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''}):
            with self.assertRaises(ModelError):
                ChatSession(OpenAIBackend()).send('你好')

    def test_real_adapter_with_fake_sdk(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(status='completed', usage=None, output_text='模拟 SDK 返回',
            output=[SimpleNamespace(model_dump=lambda **_: {'role': 'assistant', 'content': '模拟 SDK 返回'})])
        factory = MagicMock()
        factory.return_value.__enter__.return_value = client
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-placeholder', 'OPENAI_MODEL': 'test-model'}), patch('openai.OpenAI', factory):
            result = ChatSession(OpenAIBackend()).send('你好')
        self.assertEqual(result, '模拟 SDK 返回')
        self.assertEqual(client.responses.create.call_args.kwargs['model'], 'test-model')
        self.assertEqual(client.responses.create.call_args.kwargs['input'], [{'role': 'user', 'content': '你好'}])

    def test_real_adapter_hides_raw_exception(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        factory = MagicMock(side_effect=RuntimeError('不应该出现在界面中的原始错误'))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-placeholder', 'OPENAI_MODEL': 'test-model'}), patch('openai.OpenAI', factory):
            with self.assertRaises(ModelError) as caught:
                ChatSession(OpenAIBackend()).send('你好')
        self.assertNotIn('原始错误', str(caught.exception))
