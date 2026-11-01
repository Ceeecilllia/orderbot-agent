"""公开发行默认不请求模型；测试不加载开发者的 .env。"""
import os
import unittest
from unittest.mock import patch
from orderbot.config import Settings


class PublicDefaultsTests(unittest.TestCase):
    def test_clean_environment_defaults_to_simulation(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.mode, 'demo')
        self.assertEqual(settings.provider, 'zhipu')
        self.assertEqual(settings.zhipu_api_key, '')
        self.assertEqual(settings.api_key, '')

    def test_default_panel_creation_never_requests_model(self):
        import panel as pn
        from orderbot.ui import create_app
        with patch.dict(os.environ, {}, clear=True), patch('socket.socket', side_effect=AssertionError('禁止联网')), patch('openai.OpenAI', side_effect=AssertionError('禁止创建模型客户端')):
            app = create_app()
        widgets = app.select(pn.widgets.Select)
        mode = next(w for w in widgets if w.name == '运行模式（切换会清空聊天和购物车）')
        self.assertEqual(mode.value, 'demo')
