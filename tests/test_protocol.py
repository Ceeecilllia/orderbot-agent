"""真实 SDK + 本地假 HTTP 传输：只检验协议，不是实际模型验证。"""
import json
import os
import unittest
from unittest.mock import patch
import httpx
from openai import OpenAI
from orderbot.agent import AgentSession
from orderbot.cart import Cart
from orderbot.model import OpenAIBackend


class ProtocolTests(unittest.TestCase):
    def test_sdk_tool_roundtrip_without_network(self):
        captured = []
        arguments = {'product_id': 'burger', 'size': '小份', 'quantity': 3, 'toppings': []}

        def transport(request):
            payload = json.loads(request.content)
            captured.append(payload)
            if len(captured) == 1:
                output = [{'type': 'function_call', 'id': 'fc_test', 'call_id': 'call_test',
                           'name': 'add_item', 'arguments': json.dumps(arguments)}]
            else:
                output = [{'type': 'message', 'id': 'msg_test', 'role': 'assistant', 'status': 'completed',
                           'content': [{'type': 'output_text', 'text': '错误的 0 元结算', 'annotations': []}]}]
            return httpx.Response(200, json={'id': 'resp_test', 'object': 'response', 'created_at': 0,
                                            'model': 'test-model', 'status': 'completed', 'output': output})

        def factory(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))

        cart = Cart()
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-placeholder', 'OPENAI_MODEL': 'test-model'}), patch('openai.OpenAI', side_effect=factory), patch('socket.socket', side_effect=AssertionError('禁止联网')):
            session = AgentSession(OpenAIBackend(), cart)
            result = session.send('三份小汉堡')
        self.assertEqual(cart.total_cents, 2100)
        self.assertIn('21.00 EUR', result)
        self.assertNotIn('错误的 0 元', result)
        self.assertEqual(len(captured), 2)
        self.assertFalse(captured[0]['parallel_tool_calls'])
        self.assertFalse(captured[0]['store'])
        self.assertEqual(len(captured[0]['tools']), 5)
        self.assertTrue(all(t['strict'] for t in captured[0]['tools']))
        tool_output = next(i for i in captured[1]['input'] if i.get('type') == 'function_call_output')
        self.assertEqual(tool_output['call_id'], 'call_test')
        self.assertEqual(json.loads(tool_output['output'])['cart']['total_cents'], 2100)
        self.assertIn('2100', captured[1]['instructions'])

    def test_malformed_json_arguments(self):
        from orderbot.demo_model import decision
        from test_agent import ScriptedBackend
        step = decision(name='add_item')
        step['calls'][0]['arguments'] = '{bad json'
        session = AgentSession(ScriptedBackend([step]), Cart())
        reply = session.send('测试错误参数')
        self.assertIn('invalid_arguments', reply)
        self.assertEqual(session.cart.total_cents, 0)
