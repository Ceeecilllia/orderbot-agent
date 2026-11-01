"""小型 LangGraph：判断 → 工具 → 判断；结果回执由程序生成。"""
import copy
import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from .tools import BusinessTools, tool_definitions, cart_snapshot, result_text
from .menu import money
from .model import ModelError


class AgentState(TypedDict):
    messages: list[dict]  # 内部消息与工具结果，由各协议适配器转换；不向 UI 展示内部推理
    calls: list[dict]
    text: str
    rounds: int
    model_calls: int
    trace: list[dict]
    error: str
    final: str


class AgentSession:
    def __init__(self, backend, cart, max_tool_rounds=4, max_model_calls=5):
        if type(max_tool_rounds) is not int or not 1 <= max_tool_rounds <= 10:
            raise ValueError('工具轮数上限必须为 1—10 的整数。')
        if type(max_model_calls) is not int or not 1 <= max_model_calls <= 11:
            raise ValueError('模型调用上限必须为 1—11 的整数。')
        self.max_model_calls = max_model_calls
        self.backend, self.cart = backend, cart
        self.max_tool_rounds = max_tool_rounds
        self.tools = BusinessTools(cart)
        self.state = self.empty_state()
        graph = StateGraph(AgentState)
        graph.add_node('model', self.model_node)
        graph.add_node('tools', self.tools_node)
        graph.add_node('finish', self.finish_node)
        graph.add_edge(START, 'model')
        graph.add_conditional_edges('model', self.route, {'tools': 'tools', 'finish': 'finish'})
        graph.add_conditional_edges('tools', lambda s: 'finish' if s['error'] else 'model',
                                    {'finish': 'finish', 'model': 'model'})
        graph.add_edge('finish', END)
        # 不配置自动重试和 checkpointer；工具副作用不被自动重放。
        self.graph = graph.compile()

    @staticmethod
    def empty_state():
        return {'messages': [], 'calls': [], 'text': '', 'rounds': 0,
                'trace': [], 'error': '', 'final': '', 'model_calls': 0}

    def model_node(self, state):
        if state['model_calls'] >= self.max_model_calls:
            return {'calls': [], 'error': '本轮模型调用次数已达上限，已停止；请核对已执行的购物车变更。'}
        count = state['model_calls'] + 1
        try:
            decision = self.backend.decide(copy.deepcopy(state['messages']), tool_definitions(),
                                           cart_snapshot(self.cart))
            if not isinstance(decision['calls'], list) or any(
                not isinstance(c, dict) or any(not isinstance(c.get(k), str) for k in ('name', 'call_id', 'arguments'))
                for c in decision['calls']
            ):
                raise ValueError('模型工具调用格式无效。')
            return {'messages': state['messages'] + decision['items'],
                    'calls': decision['calls'], 'text': decision['text'], 'model_calls': count}
        except ModelError as exc:
            return {'calls': [], 'text': '', 'error': str(exc), 'model_calls': count}
        except Exception:
            return {'calls': [], 'text': '', 'model_calls': count, 'error':
                    '模型阶段失败（可能是配置、网络或模型权限问题）。本轮已执行的工具结果保留，请核对购物车后再操作。'}

    def route(self, state):
        return 'tools' if state['calls'] and not state['error'] else 'finish'

    def tools_node(self, state):
        messages, trace = list(state['messages']), list(state['trace'])
        blocked = state['rounds'] >= self.max_tool_rounds
        error = ''
        # 每轮最多一个工具；即使模型违规返回多调用，也不执行这一批。
        too_many = len(state['calls']) != 1
        for call in state['calls']:
            if blocked or too_many:
                result = self.tools.failure('round_limit' if blocked else 'multiple_calls',
                                            '工具调用轮数超限，本轮请求未执行。' if blocked else '每轮仅允许一个工具，这批调用未执行。')
            else:
                try:
                    args = json.loads(call['arguments'])
                except (ValueError, TypeError):
                    args = None
                result = self.tools.execute(call['name'], args)
            messages.append({'type': 'function_call_output', 'call_id': call['call_id'],
                             'output': json.dumps(result, ensure_ascii=False)})
            trace.append({'name': call['name'], 'ok': result['ok'], 'result': result})
            if not result['ok']:
                error = result['message']
        return {'messages': messages, 'trace': trace, 'calls': [],
                'rounds': state['rounds'] + (0 if blocked else 1), 'error': error}

    def finish_node(self, state):
        if state['trace']:
            # 不展示模型对执行成败/金额的自由转述，以程序回执为准。
            final = '\n\n'.join(result_text(t['name'], t['result']) for t in state['trace'])
            if state['error'] and all(t['ok'] for t in state['trace']):
                final += '\n\n' + state['error']
        elif state['error']:
            final = state['error']
        else:
            final = (state['text'] or '请提供更明确的点餐信息。') + '\n（本轮未执行购物车变更；当前总计：' + money(self.cart.total_cents) + '）'
        prefix = getattr(self.backend, 'label', '')
        final = prefix + final
        # 工具轨迹完整留在 history；另加程序回执给下一轮参考。
        return {'final': final, 'messages': state['messages'] + [{'role': 'assistant', 'content': final}]}

    @property
    def trace(self):
        return self.state['trace']

    def send(self, text):
        text = text.strip()
        if not text:
            raise ValueError('请输入消息后再发送。')
        initial = self.empty_state()
        initial['messages'] = self.state['messages'] + [{'role': 'user', 'content': text}]
        # 即便机器配置过云端追踪，也不把本地模拟/真实会话发送到 LangSmith。
        with tracing_context(enabled=False):
            self.state = self.graph.invoke(initial, {'recursion_limit': 2 * self.max_tool_rounds + 6})
        return self.state['final']

    def reset(self):
        self.cart.clear()
        if hasattr(self.backend, 'reset'):
            self.backend.reset()
        self.state = self.empty_state()
