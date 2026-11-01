"""智谱协议离线测试：实际 OpenAI SDK + MockTransport，禁止网络。"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch
import httpx
from openai import OpenAI
from orderbot.agent import AgentSession
from orderbot.cart import Cart
from orderbot.config import Settings, ConfigError
from orderbot.tools import tool_definitions
from orderbot.zhipu import ZhipuBackend, MODEL, BASE_URL, chat_tools


def reply(name=None, args=None, content='本地假回复', finish=None, call_id='call_test', model=MODEL):
    message = {'role': 'assistant', 'content': content}
    if name:
        message['content'] = None
        message['tool_calls'] = [{'id': call_id, 'type': 'function', 'function': {
            'name': name, 'arguments': args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)}}]
    return httpx.Response(200, json={'id': 'chat_test', 'object': 'chat.completion', 'created': 0,
        'model': model, 'choices': [{'index': 0, 'message': message, 'finish_reason': finish or ('tool_calls' if name else 'stop')}],
        'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}})


class ZhipuTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings.from_env({'ORDERBOT_PROVIDER': 'zhipu', 'ZHIPU_API_KEY': 'dummy-not-real'})
        self.requests = []
        self.options = []

    def transport(self, handler):
        def send(request):
            self.requests.append(request)
            return handler(json.loads(request.content))
        def factory(**kwargs):
            self.options.append({k: v for k, v in kwargs.items() if k != 'http_client'})
            kwargs.pop('http_client').close()
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(send)))
        return patch('openai.OpenAI', side_effect=factory)

    def test_config_locks_model_and_endpoint(self):
        for env in ({'ZHIPU_MODEL': 'glm-4.7'}, {'ZHIPU_BASE_URL': 'https://other.example/v1'}):
            with self.assertRaises(ConfigError):
                Settings.from_env(env)
        schema = chat_tools(tool_definitions())
        self.assertEqual(len(schema), 5)
        self.assertTrue(all('function' in t and 'strict' not in t['function'] for t in schema))
        self.assertEqual(schema[3]['function']['parameters']['required'], ['line_id'])

    def test_sdk_roundtrip_and_multi_turn_update(self):
        cart = Cart()
        def handler(payload):
            n = len(self.requests)
            if n == 1:
                return reply('query_menu')
            if n == 2:
                tool = payload['messages'][-1]
                self.assertEqual(tool['role'], 'tool')
                self.assertEqual(tool['tool_call_id'], 'call_test')
                self.assertTrue(json.loads(tool['content'])['ok'])
                return reply('add_item', {'product_id': 'burger', 'size': '大份', 'quantity': 1, 'toppings': ['cheese']}, call_id='call_add')
            if n == 3:
                self.assertEqual(payload['messages'][-1]['tool_call_id'], 'call_add')
                return reply()
            if n == 4:
                return reply('update_item', {'line_id': cart.items[0].line_id, 'quantity': 2}, call_id='call_update')
            self.assertEqual(payload['messages'][-1]['tool_call_id'], 'call_update')
            return reply()
        backend = ZhipuBackend(self.settings)
        with self.transport(handler), patch('socket.socket', side_effect=AssertionError('禁止联网')):
            session = AgentSession(backend, cart)
            self.assertIn('14.95 EUR', session.send('加一份芝士大汉堡'))
            self.assertIn('29.90 EUR', session.send('改成两份'))
        self.assertEqual(cart.items[0].toppings, ('cheese',))
        first = json.loads(self.requests[0].content)
        self.assertEqual(str(self.requests[0].url), BASE_URL + '/chat/completions')
        self.assertEqual(first['model'], MODEL)
        self.assertEqual(first['thinking'], {'type': 'disabled'})
        self.assertEqual(first['max_tokens'], 800)
        for forbidden in ('store', 'include', 'max_output_tokens', 'parallel_tool_calls'):
            self.assertNotIn(forbidden, first)
        self.assertEqual(backend.usage_records[0]['input_tokens'], 12)
        self.assertEqual(backend.usage_records[0]['total_tokens'], 20)
        self.assertTrue(all(o['max_retries'] == 0 for o in self.options))

    def test_missing_size_tool_requests_clarification(self):
        cart = Cart()
        with self.transport(lambda _: reply('add_item', {'product_id': 'burger', 'quantity': 1, 'toppings': []})):
            session = AgentSession(ZhipuBackend(self.settings), cart)
            result = session.send('一份汉堡')
        self.assertIn('请明确', result)
        self.assertEqual(cart.items, ())

    def test_malformed_arguments_and_truncation(self):
        for result in (lambda: reply('add_item', '{broken'), lambda: reply('add_item', {}, finish='length')):
            with self.subTest(result=result):
                with self.transport(lambda _: result()):
                    session = AgentSession(ZhipuBackend(self.settings), Cart())
                    session.send('test')
                self.assertTrue(session.state['error'])
                self.assertEqual(session.cart.items, ())

    def test_rate_limit_no_retry_or_fallback(self):
        backend = ZhipuBackend(replace(self.settings, max_retries=1))
        with self.transport(lambda _: httpx.Response(429, json={'error': {'code': '1302', 'message': 'dummy-not-real'}})):
            result = AgentSession(backend, Cart()).send('hi')
        self.assertEqual(len(self.requests), 1)
        self.assertIn('稍后重试', result)
        self.assertNotIn('dummy-not-real', result)
        self.assertFalse(backend.blocked)

    def test_payment_blocks_even_after_reset(self):
        backend = ZhipuBackend(self.settings)
        with self.transport(lambda _: httpx.Response(429, json={'error': {'code': '1113', 'message': '充值'}})):
            session = AgentSession(backend, Cart())
            self.assertIn('已停止', session.send('hi'))
            session.reset()
            self.assertIn('已停止', session.send('hi again'))
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(backend.blocked)

    def test_response_model_mismatch_never_executes(self):
        with self.transport(lambda _: reply('add_item', {}, model='glm-4.7')):
            backend = ZhipuBackend(self.settings)
            session = AgentSession(backend, Cart())
            self.assertIn('模型不一致', session.send('hi'))
        self.assertTrue(backend.blocked)
        self.assertEqual(session.cart.items, ())

    def test_placeholder_never_creates_client(self):
        backend = ZhipuBackend(Settings.from_env({'ZHIPU_API_KEY': 'PASTE_ZHIPU_API_KEY_HERE'}))
        with patch('openai.OpenAI', side_effect=AssertionError('不能联网')):
            result = AgentSession(backend, Cart()).send('hi')
        self.assertIn('本机 .env', result)
        self.assertEqual(backend.usage_records, [])

    def test_duplicate_or_missing_call_id_rejected(self):
        for call_id in ('',):
            with self.transport(lambda _: reply('get_cart', call_id=call_id)):
                session = AgentSession(ZhipuBackend(self.settings), Cart())
                self.assertIn('ID', session.send('hi'))
        response = reply('get_cart').json()
        response['choices'][0]['message']['tool_calls'] *= 2
        with self.transport(lambda _: httpx.Response(200, json=response)):
            session = AgentSession(ZhipuBackend(self.settings), Cart())
            self.assertIn('ID', session.send('hi'))

    def test_openai_credentials_never_used_as_zhipu_credentials(self):
        cfg = Settings.from_env({'ORDERBOT_PROVIDER': 'zhipu', 'OPENAI_API_KEY': 'dummy-openai', 'OPENAI_MODEL': 'paid-model'})
        with patch('openai.OpenAI', side_effect=AssertionError('不应使用其他服务密钥')):
            result = AgentSession(ZhipuBackend(cfg), Cart()).send('hi')
        self.assertIn('ZHIPU_API_KEY', result)

    def test_service_error_reason_is_redacted(self):
        from orderbot.zhipu import safe_service_error
        code, reason = safe_service_error({'code': '1000', 'message': '验证失败 dummy-not-real Bearer abcdefghijklmnopqrstuv https://example.test?key=anything'}, self.settings)
        self.assertEqual(code, '1000')
        self.assertIn('验证失败', reason)
        self.assertNotIn('dummy-not-real', reason)
        self.assertNotIn('abcdefghijklmnop', reason)
        self.assertNotIn('https://', reason)
