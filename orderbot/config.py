"""集中读取环境变量；不读取密钥文件，配置对象不打印密钥或地址。"""
import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


def number(env, name, default, low, high, kind=int):
    try:
        value = kind(env.get(name, str(default)))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError
        return value
    except (ValueError, TypeError, OverflowError):
        raise ConfigError(f'{name} 必须在 {low}—{high} 范围内。') from None


@dataclass(frozen=True)
class Settings:
    mode: str = 'demo'
    provider: str = 'zhipu'
    zhipu_api_key: str = field(default='', repr=False)
    zhipu_model: str = 'glm-4.7-flash'
    zhipu_base_url: str = 'https://open.bigmodel.cn/api/paas/v4'
    model: str = ''
    api_key: str = field(default='', repr=False)
    base_url: str = field(default='https://api.openai.com/v1', repr=False)
    timeout_seconds: float = 20
    max_retries: int = 0
    max_output_tokens: int = 800
    max_tool_rounds: int = 4
    max_model_calls: int = 5
    max_session_requests: int = 20
    max_input_chars: int = 40000

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        mode = env.get('ORDERBOT_MODE', 'demo').strip().lower()
        if mode not in ('fake', 'demo', 'real'):
            raise ConfigError('ORDERBOT_MODE 只能是 fake、demo 或 real。')
        provider = env.get('ORDERBOT_PROVIDER', 'zhipu').strip().lower()
        if provider not in ('openai', 'zhipu'):
            raise ConfigError('ORDERBOT_PROVIDER 只能是 openai 或 zhipu。')
        zhipu_model = env.get('ZHIPU_MODEL', 'glm-4.7-flash').strip()
        zhipu_base_url = env.get('ZHIPU_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4').strip().rstrip('/')
        if zhipu_model != 'glm-4.7-flash' or zhipu_base_url != 'https://open.bigmodel.cn/api/paas/v4':
            raise ConfigError('智谱仅允许 glm-4.7-flash 和官方 https://open.bigmodel.cn/api/paas/v4，不会切换收费模型或服务。')
        base_url = env.get('OPENAI_BASE_URL', '').strip() or 'https://api.openai.com/v1'
        try:
            url = urlsplit(base_url)
            local = url.hostname in ('localhost', '127.0.0.1', '::1')
            if (not url.hostname or url.username or url.password or url.query or url.fragment
                    or not (url.scheme == 'https' or (url.scheme == 'http' and local))):
                raise ValueError
            _ = url.port
        except ValueError:
            raise ConfigError('OPENAI_BASE_URL 必须为无账号、密码、查询参数的 HTTPS 地址（本机可用 HTTP）。') from None
        return cls(
            mode=mode, provider=provider, zhipu_api_key=env.get('ZHIPU_API_KEY', '').strip(),
            zhipu_model=zhipu_model, zhipu_base_url=zhipu_base_url, model=env.get('OPENAI_MODEL', '').strip(),
            api_key=env.get('OPENAI_API_KEY', '').strip(), base_url=base_url,
            timeout_seconds=number(env, 'ORDERBOT_TIMEOUT_SECONDS', 20, 1, 60, float),
            max_retries=number(env, 'ORDERBOT_MAX_RETRIES', 0, 0, 1),
            max_output_tokens=number(env, 'ORDERBOT_MAX_OUTPUT_TOKENS', 800, 64, 4096),
            max_tool_rounds=number(env, 'ORDERBOT_MAX_TOOL_ROUNDS', 4, 1, 10),
            max_model_calls=number(env, 'ORDERBOT_MAX_MODEL_CALLS', 5, 1, 11),
            max_session_requests=number(env, 'ORDERBOT_MAX_SESSION_REQUESTS', 20, 1, 200),
            max_input_chars=number(env, 'ORDERBOT_MAX_INPUT_CHARS', 40000, 1000, 100000),
        )
