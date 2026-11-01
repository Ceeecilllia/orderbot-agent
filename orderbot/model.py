"""模型适配；默认假回复，真实模型仅在主动发送时调用。"""
import json
from .config import Settings, ConfigError

SYSTEM_PROMPT = (
    '你是 My Dear Frankfurt 中文点餐助手。必须通过工具查询菜单、购物车和增删改商品。'
    '不清楚商品规格、数量或修改/删除对象时先询问，绝不能猜测。相同商品不同配料是不同的行。'
    '使用当前购物车的完整 line_id；不能使用过时的行编号，不能把界面选中项当作用户意图。'
    '每次只调用一个工具，依据结果决定是否继续；工具报错不得声称成功，不得自动重复添加。'
    '用户仅修改数量时 size 和 toppings 传 null，以保留原值。新增时无配料传 []。'
    '价格和总额完全以工具返回的整数分为准，不能自己改价、估价或伪造成功。'
    '不接支付、不提交订单。工具执行信息和程序回执会单独显示，不输出内部推理。'
)

class ModelError(Exception):
    pass

class FakeBackend:
    def reply(self, messages):
        return '【假回复 / 未调用模型】已收到消息。此模式仅检查聊天界面；请用右侧控件选择商品和修改购物车。'

class OpenAIBackend:
    """只支持 Responses 协议。记录白名单用量字段，不记录原始请求/异常。"""
    label = '【真实模型模式】\n'

    def __init__(self, settings=None):
        self.settings = settings
        self.usage_records = []

    def reset(self):
        self.usage_records.clear()

    def reply(self, messages):
        # 保留普通聊天适配入口，复用同一套配置、限制和错误处理。
        return self.decide(messages, [], {'currency': 'EUR', 'items': [], 'total_cents': 0})['text']

    def decide(self, messages, tools, cart):
        try:
            settings = self.settings or Settings.from_env()
        except ConfigError as exc:
            raise ModelError(str(exc)) from None
        if not settings.api_key or not settings.model:
            raise ModelError('真实模式缺少 OPENAI_API_KEY 或 OPENAI_MODEL；没有发送请求。')
        if len(self.usage_records) >= settings.max_session_requests:
            raise ModelError('本会话模型请求次数已达上限；请核对用量后再开始新对话。')
        instructions = SYSTEM_PROMPT + '\n当前购物车快照：' + json.dumps(cart, ensure_ascii=False)
        if len(json.dumps(messages, ensure_ascii=False)) + len(instructions) > settings.max_input_chars:
            raise ModelError('对话内容超过输入长度限制；请开始新对话，不会自动裁剪工具历史。')
        record = {'request': len(self.usage_records) + 1, 'status': 'pending',
                  'input_tokens': None, 'output_tokens': None, 'total_tokens': None}
        self.usage_records.append(record)
        try:
            import openai
            with openai.OpenAI(api_key=settings.api_key, base_url=settings.base_url,
                               timeout=settings.timeout_seconds, max_retries=settings.max_retries) as client:
                response = client.responses.create(
                    model=settings.model, instructions=instructions, input=messages,
                    tools=tools, parallel_tool_calls=False, store=False,
                    include=['reasoning.encrypted_content'],
                    max_output_tokens=settings.max_output_tokens,
                )
            usage = getattr(response, 'usage', None)
            for field in ('input_tokens', 'output_tokens', 'total_tokens'):
                value = getattr(usage, field, None)
                if type(value) is int and value >= 0:
                    record[field] = value
            # 截断的 JSON 或尚未完成的响应不能执行工具，即便含部分调用项。
            if response.status != 'completed':
                record['status'] = 'incomplete'
                raise ModelError('模型响应未完成或被截断，未执行本次工具请求；可核对输出上限后重试。')
            items = [item.model_dump(exclude_none=True) for item in response.output]
            calls = [item for item in items if item.get('type') == 'function_call']
            if not calls and not response.output_text:
                record['status'] = 'empty'
                raise ModelError('模型未返回文字或工具请求，未执行操作。')
            record['status'] = 'completed'
            return {'text': response.output_text, 'calls': calls, 'items': items}
        except ModelError:
            raise
        except Exception as exc:
            # 只使用异常类型/状态码分类，绝不显示原始异常、URL、请求头或 key。
            import openai
            if isinstance(exc, openai.APITimeoutError):
                category, message = 'timeout', '模型请求超时，请先核对购物车与用量。'
            elif isinstance(exc, openai.APIConnectionError):
                category, message = 'connection', '无法连接模型服务，请检查服务地址或网络。'
            elif isinstance(exc, openai.APIStatusError):
                code = exc.status_code
                category = f'http_{code}'
                message = {
                    400: '模型服务拒绝参数或工具协议，请检查 Responses 兼容性。',
                    401: '模型服务认证失败，请检查 API Key。',
                    403: '模型服务拒绝访问，请检查权限。',
                    404: '找不到模型或接口，请检查模型名和服务地址。',
                    429: '模型服务限流或额度不足，请检查服务端用量。',
                }.get(code, '模型服务返回错误，请稍后检查服务状态。')
            else:
                category, message = 'protocol', '模型响应解析或客户端配置失败，请检查接口兼容性。'
            record['status'] = category
            raise ModelError(message + ' 未切换到模拟模式。') from None


class ChatSession:
    def __init__(self, backend):
        self.backend = backend
        self.messages = []

    def send(self, text):
        text = text.strip()
        if not text:
            raise ValueError('请输入消息后再发送。')
        proposed = self.messages + [{'role': 'user', 'content': text}]
        reply = self.backend.reply(proposed)
        # 请求成功后才更新历史，失败可重试。
        self.messages = proposed + [{'role': 'assistant', 'content': reply}]
        return reply

    def reset(self):
        self.messages.clear()
