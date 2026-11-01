"""智谱 glm-4.7-flash：Chat Completions ↔ 既有 LangGraph 消息。"""
import copy
import json
import re
from .config import Settings
from .model import OpenAIBackend, ModelError, SYSTEM_PROMPT

MODEL = 'glm-4.7-flash'
BASE_URL = 'https://open.bigmodel.cn/api/paas/v4'
PLACEHOLDER = 'PASTE_ZHIPU_API_KEY_HERE'


def chat_tools(definitions):
    """复用业务工具名和字段，转换为智谱文档中的嵌套 function 格式。"""
    result = []
    required = {'query_menu': [], 'get_cart': [],
                'add_item': ['product_id', 'size', 'quantity', 'toppings'],
                'update_item': ['line_id'], 'remove_item': ['line_id']}
    for tool in definitions:
        schema = copy.deepcopy(tool['parameters'])
        schema['required'] = required[tool['name']]
        for prop in schema.get('properties', {}).values():
            if 'anyOf' in prop:
                variants = prop.pop('anyOf')
                prop.update(next(v for v in variants if v.get('type') != 'null'))
        result.append({'type': 'function', 'function': {
            'name': tool['name'], 'description': tool['description'], 'parameters': schema}})
    return result


def chat_messages(messages):
    """工具结果必须 role=tool 且 tool_call_id 与 assistant 的 id 完全配对。"""
    result = []
    for item in messages:
        if item.get('type') == 'function_call_output':
            result.append({'role': 'tool', 'tool_call_id': item['call_id'], 'content': item['output']})
        elif item.get('type') == 'function_call':
            call = {'id': item['call_id'], 'type': 'function',
                    'function': {'name': item['name'], 'arguments': item['arguments']}}
            if result and result[-1].get('tool_calls'):
                result[-1]['tool_calls'].append(call)
            else:
                result.append({'role': 'assistant', 'content': None, 'tool_calls': [call]})
        elif item.get('role') in ('user', 'assistant'):
            result.append({k: copy.deepcopy(item[k]) for k in
                           ('role', 'content', 'tool_calls', 'reasoning_content') if k in item})
        else:
            raise ModelError('当前消息格式不支持智谱 Chat Completions，请开始新对话。')
    return result


def safe_service_error(error, settings):
    """只返回短业务码和脱敏原因，不返回 HTTP 正文或认证字段。"""
    if not isinstance(error, dict):
        return '', '服务端未提供可安全展示的原因'
    code = str(error.get('code', ''))
    code = code if re.fullmatch(r'\d{1,6}', code) else ''
    text = error.get('message', '')
    if not isinstance(text, str):
        text = ''
    for secret in (settings.zhipu_api_key, settings.api_key):
        if secret:
            text = text.replace(secret, '[已隐藏]')
    text = re.sub(r'(?i)bearer\s+\S+', '[认证信息已隐藏]', text)
    text = re.sub(r'https?://\S+', '[地址已隐藏]', text)
    text = re.sub(r'[A-Za-z0-9_-]{16,}(?:\.[A-Za-z0-9_-]+)*', '[标识已隐藏]', text)
    text = ' '.join(text.split())[:180]
    return code, text or '服务端未提供可安全展示的原因'


class ZhipuBackend(OpenAIBackend):
    label = '【真实模型模式 / 智谱 glm-4.7-flash】\n'

    def __init__(self, settings=None):
        super().__init__(settings)
        self.blocked = False

    # 不继承普通 reply：该入口也必须走固定智谱模型。
    def reply(self, messages):
        return self.decide(messages, [], {'currency': 'EUR', 'items': [], 'total_cents': 0})['text']

    def reset(self):
        # 欠费/套餐阻断不因“新对话”解除；需停下来检查账户，重启才解除本地闩锁。
        super().reset()

    def decide(self, messages, tools, cart):
        import openai
        settings = self.settings or Settings.from_env()
        if settings.zhipu_model != MODEL or settings.zhipu_base_url.rstrip('/') != BASE_URL:
            raise ModelError('智谱模型或地址不符合免费限定，已停止。')
        if self.blocked:
            raise ModelError('账户出现付费/套餐或模型不匹配提示，已停止当前智谱会话请求。请勿充值或购买套餐。')
        if not settings.zhipu_api_key or settings.zhipu_api_key == PLACEHOLDER:
            raise ModelError('请在本机 .env 的 ZHIPU_API_KEY 一行填写智谱密钥；当前没有发送请求。')
        if len(self.usage_records) >= settings.max_session_requests:
            raise ModelError('本会话模型请求次数已达上限，请核对用量后再开始新对话。')
        converted = [{'role': 'system', 'content': SYSTEM_PROMPT + '\n当前购物车快照：' + json.dumps(cart, ensure_ascii=False)}] + chat_messages(messages)
        if len(json.dumps(converted, ensure_ascii=False)) > settings.max_input_chars:
            raise ModelError('对话内容超过输入长度限制，请开始新对话。')
        record = {'request': len(self.usage_records) + 1, 'status': 'pending',
                  'input_tokens': None, 'output_tokens': None, 'total_tokens': None}
        self.usage_records.append(record)
        try:
            # 智谱分支固定零自动重试：429 可能是限流，也可能是欠费。
            # 关闭重定向，避免将认证请求转发到其他服务。
            import httpx
            with openai.OpenAI(api_key=settings.zhipu_api_key, base_url=BASE_URL,
                               timeout=settings.timeout_seconds, max_retries=0,
                               http_client=httpx.Client(follow_redirects=False, timeout=settings.timeout_seconds)) as client:
                params = {'model': MODEL, 'messages': converted, 'stream': False,
                          'max_tokens': settings.max_output_tokens,
                          'extra_body': {'thinking': {'type': 'disabled'}}}
                if tools:
                    params.update(tools=chat_tools(tools), tool_choice='auto')
                response = client.chat.completions.create(**params)
            usage = getattr(response, 'usage', None)
            for target, source in [('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'), ('total_tokens', 'total_tokens')]:
                value = getattr(usage, source, None)
                if type(value) is int and value >= 0:
                    record[target] = value
            if response.model.lower() != MODEL:
                self.blocked = True
                record['status'] = 'model_mismatch'
                raise ModelError('服务返回的模型与固定免费模型不一致，已停止，不执行工具。')
            choice = response.choices[0]
            if choice.finish_reason not in ('stop', 'tool_calls'):
                record['status'] = 'incomplete'
                raise ModelError('智谱响应未正常完成或被截断，本次工具请求未执行。')
            message = choice.message
            raw_calls = message.tool_calls or []
            if (raw_calls and choice.finish_reason != 'tool_calls') or (not raw_calls and choice.finish_reason == 'tool_calls'):
                raise ModelError('智谱工具结束状态与调用列表不一致，未执行工具。')
            ids, calls, wire_calls = set(), [], []
            for call in raw_calls:
                if (call.type != 'function' or not call.id or call.id in ids
                        or not isinstance(call.function.arguments, str) or not call.function.name):
                    raise ModelError('智谱工具调用 ID 或格式无效，未执行工具。')
                ids.add(call.id)
                calls.append({'call_id': call.id, 'name': call.function.name, 'arguments': call.function.arguments})
                wire_calls.append({'id': call.id, 'type': 'function', 'function': {
                    'name': call.function.name, 'arguments': call.function.arguments}})
            item = {'role': 'assistant', 'content': message.content}
            if wire_calls:
                item['tool_calls'] = wire_calls
            # 通常 thinking=disabled 不返回该项；若返回，仅原样保留供协议回传，不展示。
            reasoning = getattr(message, 'reasoning_content', None)
            if reasoning:
                item['reasoning_content'] = reasoning
            if not calls and not message.content:
                record['status'] = 'empty'
                raise ModelError('智谱未返回文字或工具调用。')
            record['status'] = 'completed'
            return {'text': message.content or '', 'calls': calls, 'items': [item]}
        except ModelError:
            if record['status'] == 'pending':
                record['status'] = 'protocol'
            raise
        except Exception as exc:
            if isinstance(exc, openai.APIStatusError):
                body = exc.body if isinstance(exc.body, dict) else {}
                error = body.get('error', body)
                code, reason = safe_service_error(error, settings)
                record.update(http_status=exc.status_code, service_error_code=code, service_reason=reason)
                detail = f' HTTP {exc.status_code}；服务端业务码 {code or "未提供"}；原因：{reason}'
                if exc.status_code == 402 or code in {'1113', '1309', '1311', '1314', '1315', '1316', '1317', '1318', '1319', '1320', '1321'}:
                    self.blocked = True
                    record['status'] = 'payment_blocked'
                    raise ModelError('账户要求付费、余额或套餐权限，已停止。不会充值、购买套餐或切换模型/服务。' + detail) from None
                record['status'] = f'http_{exc.status_code}'
                if exc.status_code == 429:
                    message = '智谱当前限流或达到免费使用上限，请稍后重试；不会自动重试或切换模型。'
                elif exc.status_code in (401, 403):
                    message = '智谱密钥或权限校验失败，请在本机检查 API Key；不会切换服务。'
                else:
                    message = '智谱接口返回错误，已停止本轮；请检查接口状态，不会切换模型或服务。'
                message += detail
            elif isinstance(exc, openai.APITimeoutError):
                record['status'], message = 'timeout', '智谱请求超时，请稍后手动重试并先核对购物车。'
            else:
                record['status'], message = 'connection_or_protocol', '智谱连接或响应格式异常，已停止本轮，不会切换服务。'
            raise ModelError(message) from None
