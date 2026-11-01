"""所有 Agent 测试使用规则/脚本替身；绝不发送真实模型请求。"""
import json
import unittest
from unittest.mock import patch
from orderbot.cart import Cart
from orderbot.tools import BusinessTools, tool_definitions
from orderbot.agent import AgentSession
from orderbot.demo_model import DemoBackend, decision


class ScriptedBackend:
    label = '【脚本替身】'

    def __init__(self, steps):
        self.steps = list(steps)
        self.inputs = []

    def decide(self, messages, tools, cart):
        self.inputs.append(messages)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()
        self.session = AgentSession(DemoBackend(), self.cart)

    def test_demo_menu_add_update_remove_no_network(self):
        with patch('socket.socket', side_effect=AssertionError('禁止联网')):
            self.assertIn('12.95 EUR', self.session.send('查看菜单'))
            self.session.send('添加一份大份汉堡，加芝士')
            self.assertEqual(self.cart.total_cents, 1495)
            self.assertEqual(self.session.trace[0]['name'], 'add_item')
            self.session.send('把第1行改成3份')
            self.assertEqual(self.cart.total_cents, 4485)
            self.session.send('删除第1行')
            self.assertEqual(self.cart.total_cents, 0)
            self.assertIn('规则替身', self.session.state['final'])

    def test_missing_size_asks_without_mutation(self):
        reply = self.session.send('添加一份汉堡，不加配料')
        self.assertIn('请明确', reply)
        self.assertEqual(self.session.trace[0]['result']['code'], 'needs_clarification')
        self.assertEqual(self.cart.items, ())

    def test_same_product_different_toppings_and_ambiguous_update(self):
        self.session.send('添加一份大份汉堡，加芝士')
        self.session.send('添加一份大份汉堡，加蘑菇')
        before = self.cart.items
        self.assertEqual(self.cart.total_cents, 2940)
        self.assertNotEqual(before[0].line_id, before[1].line_id)
        self.assertIn('请明确要修改哪一行', self.session.send('把汉堡改成3份'))
        self.assertEqual(self.cart.items, before)
        self.session.send('把第2行改成2份')
        self.assertEqual([i.quantity for i in self.cart.items], [1, 2])
        self.assertEqual(self.cart.total_cents, 4385)

    def test_delete_nonexistent(self):
        reply = self.session.send('删除行 missing')
        self.assertIn('没有这条商品', reply)
        self.assertFalse(self.session.trace[0]['ok'])
        self.assertNotIn('已删除', reply)

    def test_invalid_quantity(self):
        for value in ('0', '-1', '100', '1.5'):
            with self.subTest(value=value):
                reply = self.session.send(f'添加{value}份小份汉堡，不加配料')
                self.assertFalse(self.session.trace[0]['ok'])
                self.assertEqual(self.cart.items, ())
                self.assertNotIn('已添加', reply)

    def test_tool_exception_is_safe(self):
        with patch.object(self.cart, 'add_item', side_effect=RuntimeError('private details')):
            reply = self.session.send('添加一份大份汉堡，加芝士')
        self.assertEqual(self.session.trace[0]['result']['code'], 'tool_exception')
        self.assertNotIn('private details', reply)
        self.assertNotIn('已添加', reply)

    def test_langgraph_loops_and_returns_tool_result_to_model(self):
        backend = ScriptedBackend([
            decision(name='query_menu', args={'product_id': 'burger'}),
            decision(name='add_item', args={'product_id': 'burger', 'size': '小份', 'quantity': 2, 'toppings': []}),
            decision('已添加，价格 0 元（错误转述不应显示）'),
        ])
        session = AgentSession(backend, self.cart)
        reply = session.send('加两份小汉堡')
        self.assertEqual(session.state['rounds'], 2)
        self.assertEqual(self.cart.total_cents, 1400)
        output = json.loads(backend.inputs[1][-1]['output'])
        self.assertTrue(output['ok'])
        self.assertIn('menu', output)
        self.assertIn('14.00 EUR', reply)
        self.assertNotIn('价格 0 元', reply)

    def test_failure_stops_before_false_success(self):
        backend = ScriptedBackend([
            decision(name='remove_item', args={'line_id': 'missing'}),
            decision('操作成功'),
        ])
        session = AgentSession(backend, self.cart)
        self.assertNotIn('操作成功', session.send('删除不存在行'))
        self.assertEqual(len(backend.inputs), 1)
        self.assertEqual(len(backend.steps), 1)

    def test_round_limit_and_balanced_tool_outputs(self):
        class Loop:
            def decide(self, *args):
                return decision(name='get_cart')
        session = AgentSession(Loop(), self.cart, max_tool_rounds=2)
        reply = session.send('测试循环')
        self.assertEqual(session.state['rounds'], 2)
        self.assertEqual(len(session.trace), 3)
        self.assertEqual(session.trace[-1]['result']['code'], 'round_limit')
        self.assertIn('轮数超限', reply)
        messages = session.state['messages']
        self.assertEqual(sum(i.get('type') == 'function_call' for i in messages),
                         sum(i.get('type') == 'function_call_output' for i in messages))

    def test_partial_success_then_model_failure_keeps_receipt(self):
        backend = ScriptedBackend([
            decision(name='add_item', args={'product_id': 'burger', 'size': '小份', 'quantity': 1, 'toppings': []}),
            RuntimeError('model failure'),
        ])
        session = AgentSession(backend, self.cart)
        reply = session.send('加一份小汉堡')
        self.assertEqual(self.cart.total_cents, 700)
        self.assertIn('已添加', reply)
        self.assertIn('模型阶段失败', reply)
        self.assertEqual(len(session.trace), 1)

    def test_reset_and_isolation(self):
        self.session.send('添加一份大份汉堡，加芝士')
        other = AgentSession(DemoBackend(), Cart())
        self.assertEqual(other.cart.total_cents, 0)
        self.assertEqual(other.state['messages'], [])
        self.session.reset()
        self.assertEqual(self.session.state, self.session.empty_state())
        self.assertEqual(self.cart.total_cents, 0)

    def test_multiple_calls_rejected_without_mutation(self):
        one = decision(name='add_item', args={'product_id': 'burger', 'size': '小份', 'quantity': 1, 'toppings': []})
        two = decision(name='remove_item', args={'line_id': 'missing'})
        one['calls'] += two['calls']
        one['items'] += two['items']
        session = AgentSession(ScriptedBackend([one]), self.cart)
        session.send('并行变更')
        self.assertEqual(self.cart.total_cents, 0)
        self.assertTrue(all(t['result']['code'] == 'multiple_calls' for t in session.trace))


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()
        self.tools = BusinessTools(self.cart)

    def test_prices_cannot_be_injected(self):
        result = self.tools.execute('add_item', {'product_id': 'burger', 'size': '小份',
                                                'quantity': 1, 'toppings': [], 'unit_cents': 1})
        self.assertFalse(result['ok'])
        self.assertEqual(self.cart.items, ())

    def test_patch_preserves_other_fields(self):
        item = self.cart.add_item('burger', '大份', ['cheese'], 1)
        result = self.tools.execute('update_item', {'line_id': item.line_id, 'quantity': 2})
        self.assertTrue(result['ok'])
        self.assertEqual(self.cart.items[0].toppings, ('cheese',))
        self.assertEqual(self.cart.total_cents, 2990)
        result = self.tools.execute('update_item', {'line_id': item.line_id, 'toppings': []})
        self.assertEqual(self.cart.total_cents, 2590)

    def test_invalid_parameters_unknown_tool_and_missing_target(self):
        for args in ({'line_id': None}, {'quantity': True}, {'quantity': '2'}, None):
            self.assertFalse(self.tools.execute('update_item', args)['ok'])
        self.assertEqual(self.tools.execute('remove_item', {})['code'], 'needs_clarification')
        self.assertEqual(self.tools.execute('hacked_tool', {})['code'], 'unknown_tool')

    def test_strict_schema_required_nullable_fields(self):
        for tool in tool_definitions():
            schema = tool['parameters']
            self.assertTrue(tool['strict'])
            self.assertFalse(schema['additionalProperties'])
            self.assertEqual(set(schema['required']), set(schema['properties']))
            self.assertNotIn('price', schema['properties'])
