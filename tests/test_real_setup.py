"""真实接入准备：只使用本地假 HTTP/规则替身，不读取 .env 或真实密钥。"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch
import httpx
from openai import OpenAI
from orderbot.config import Settings, ConfigError
from orderbot.model import OpenAIBackend, ModelError
from orderbot.agent import AgentSession
from orderbot.cart import Cart
from orderbot.demo_model import decision


def response(output=None, usage=None, status='completed'):
    if output is None:
        output = [{'type': 'message', 'id': 'm_test', 'role': 'assistant', 'status': 'completed',
                   'content': [{'type': 'output_text', 'text': '本地假响应', 'annotations': []}]}]
    return httpx.Response(200, json={'id': 'resp_test', 'object': 'response', 'created_at': 0,
                                    'status': status, 'model': 'test-model', 'output': output, 'usage': usage})


class ConfigTests(unittest.TestCase):
    def test_defaults_and_secrets_not_in_repr(self):
        cfg = Settings.from_env({'OPENAI_API_KEY': 'dummy-not-real'})
        self.assertEqual(cfg.mode, 'demo')
        self.assertEqual(cfg.max_retries, 0)
        self.assertNotIn('dummy-not-real', repr(cfg))
        self.assertEqual(cfg.base_url, 'https://api.openai.com/v1')

    def test_invalid_limits(self):
        for name, value in [('ORDERBOT_MODE', 'typo'), ('ORDERBOT_TIMEOUT_SECONDS', 'nan'),
                            ('ORDERBOT_MAX_RETRIES', '2'), ('ORDERBOT_MAX_TOOL_ROUNDS', '0'),
                            ('ORDERBOT_MAX_MODEL_CALLS', '12'), ('ORDERBOT_MAX_OUTPUT_TOKENS', '0'),
                            ('ORDERBOT_MAX_SESSION_REQUESTS', '-1'), ('ORDERBOT_MAX_INPUT_CHARS', '1')]:
            with self.subTest(name=name), self.assertRaises(ConfigError):
                Settings.from_env({name: value})

    def test_url_validation_without_leaking_values(self):
        for url in ('https://user:dummy-not-real@example.test/v1',
                    'https://example.test/v1?key=dummy-not-real', 'http://example.test/v1'):
            with self.assertRaises(ConfigError) as caught:
                Settings.from_env({'OPENAI_BASE_URL': url})
            self.assertNotIn('dummy-not-real', str(caught.exception))
        self.assertEqual(Settings.from_env({'OPENAI_BASE_URL': 'http://localhost:9999/v1'}).base_url,
                         'http://localhost:9999/v1')


class RealSetupTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings.from_env({'OPENAI_MODEL': 'test-model', 'OPENAI_API_KEY': 'dummy-not-real',
                                      'OPENAI_BASE_URL': 'https://gateway.example.test/v1'})
        self.requests = []
        self.client_options = []

    def transport_context(self, handler):
        def transport(request):
            self.requests.append(request)
            return handler(request)
        def factory(**kwargs):
            self.client_options.append(kwargs)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        return patch('openai.OpenAI', side_effect=factory)

    def test_limits_endpoint_and_usage_whitelist(self):
        backend = OpenAIBackend(self.cfg)
        usage = {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15,
                 'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0},
                 'unexpected': 'dummy-not-real'}
        with self.transport_context(lambda _: response(usage=usage)), patch('socket.socket', side_effect=AssertionError('禁止联网')):
            backend.decide([{'role': 'user', 'content': 'hi'}], [], {'items': [], 'total_cents': 0})
        payload = json.loads(self.requests[0].content)
        self.assertEqual(str(self.requests[0].url), 'https://gateway.example.test/v1/responses')
        self.assertEqual(payload['max_output_tokens'], 800)
        self.assertEqual(self.client_options[0]['timeout'], 20)
        self.assertEqual(self.client_options[0]['max_retries'], 0)
        self.assertEqual(backend.usage_records, [{'request': 1, 'status': 'completed',
                                               'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}])
        self.assertNotIn('dummy-not-real', json.dumps(backend.usage_records))

    def test_missing_usage_is_unknown(self):
        backend = OpenAIBackend(self.cfg)
        with self.transport_context(lambda _: response()):
            backend.reply([{'role': 'user', 'content': 'hi'}])
        self.assertIsNone(backend.usage_records[0]['total_tokens'])

    def test_incomplete_response_never_executes_tool(self):
        call = {'type': 'function_call', 'id': 'fc_test', 'call_id': 'call_test', 'name': 'add_item',
                'arguments': json.dumps({'product_id': 'burger', 'size': '小份', 'quantity': 1, 'toppings': []})}
        backend = OpenAIBackend(self.cfg)
        cart = Cart()
        with self.transport_context(lambda _: response(output=[call], status='incomplete')):
            session = AgentSession(backend, cart)
            reply = session.send('加一份汉堡')
        self.assertEqual(cart.items, ())
        self.assertEqual(session.trace, [])
        self.assertIn('截断', reply)
        self.assertNotIn('规则替身', reply)
        self.assertEqual(backend.usage_records[0]['status'], 'incomplete')

    def test_http_errors_are_redacted_and_never_fallback(self):
        for code in (400, 401, 403, 404, 429, 500):
            with self.subTest(code=code):
                backend = OpenAIBackend(self.cfg)
                with self.transport_context(lambda _: httpx.Response(code, json={'error': {'message': 'dummy-not-real'}})):
                    session = AgentSession(backend, Cart())
                    reply = session.send('hi')
                self.assertTrue(session.state['error'])
                self.assertIn('真实模型模式', reply)
                self.assertIn('未切换到模拟模式', reply)
                self.assertNotIn('dummy-not-real', reply)
                self.assertEqual(backend.usage_records[0]['status'], f'http_{code}')

    def test_timeout_is_reported_without_retry_by_default(self):
        backend = OpenAIBackend(self.cfg)
        def timeout(request):
            raise httpx.ReadTimeout('dummy-not-real', request=request)
        with self.transport_context(timeout), self.assertRaises(ModelError) as caught:
            backend.reply([{'role': 'user', 'content': 'hi'}])
        self.assertIn('超时', str(caught.exception))
        self.assertNotIn('dummy-not-real', str(caught.exception))
        self.assertEqual(len(self.requests), 1)

    def test_finite_retry_only_for_request_not_business_tools(self):
        backend = OpenAIBackend(replace(self.cfg, max_retries=1))
        with self.transport_context(lambda _: httpx.Response(500, json={'error': {'message': 'temporary'}})), self.assertRaises(ModelError):
            backend.reply([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(backend.usage_records), 1)

    def test_auth_error_not_retried(self):
        backend = OpenAIBackend(replace(self.cfg, max_retries=1))
        with self.transport_context(lambda _: httpx.Response(401, json={'error': {'message': 'invalid'}})), self.assertRaises(ModelError):
            backend.reply([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(len(self.requests), 1)

    def test_session_cap_and_reset(self):
        backend = OpenAIBackend(replace(self.cfg, max_session_requests=1))
        with self.transport_context(lambda _: response()):
            backend.reply([{'role': 'user', 'content': 'hi'}])
            with self.assertRaises(ModelError):
                backend.reply([{'role': 'user', 'content': 'again'}])
        self.assertEqual(len(self.requests), 1)
        AgentSession(backend, Cart()).reset()
        self.assertEqual(backend.usage_records, [])

    def test_input_cap_does_not_send_request(self):
        backend = OpenAIBackend(replace(self.cfg, max_input_chars=1000))
        with patch('openai.OpenAI', side_effect=AssertionError('禁止请求')), self.assertRaises(ModelError):
            backend.reply([{'role': 'user', 'content': 'x' * 1001}])
        self.assertEqual(backend.usage_records, [])

    def test_model_call_cap_stops_graph(self):
        class Loop:
            calls = 0
            def decide(self, *args):
                self.calls += 1
                return decision(name='get_cart')
        backend = Loop()
        session = AgentSession(backend, Cart(), max_model_calls=1)
        reply = session.send('loop')
        self.assertEqual(backend.calls, 1)
        self.assertEqual(session.state['model_calls'], 1)
        self.assertIn('模型调用次数已达上限', reply)
