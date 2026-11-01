import asyncio
import os
import unittest
from unittest.mock import patch

try:
    import panel as pn
except ImportError:
    pn = None

@unittest.skipIf(pn is None, '需要安装 Panel 后验证界面')
class UITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from orderbot.ui import create_app
        with patch.dict(os.environ, {'ORDERBOT_MODE': 'fake'}), patch('socket.socket', side_effect=AssertionError('启动禁止联网')):
            self.app = create_app()

    def widget(self, name):
        return next(w for w in self.app.select(pn.widgets.Widget) if w.name == name)

    def text(self):
        panes = self.app.select(pn.pane.Str)
        # Panel 1.10 对折叠的 Accordion 不递归 select，直接检查记录对象。
        for accordion in self.app.select(pn.Accordion):
            panes += [p for p in accordion.objects if isinstance(p, pn.pane.Str)]
        return '\n'.join(str(p.object) for p in panes)

    async def click_send(self):
        button = self.widget('发送')
        callback = button.param.watchers['clicks']['value'][-1].fn
        await callback(None)

    async def test_cart_controls_and_reset(self):
        self.widget('商品编号 / 名称').value = 'burger'
        self.widget('规格').value = '大份'
        self.widget('配料（每份加价）').value = ['cheese']
        self.widget('数量（1—99）').value = 2
        self.widget('添加为新商品').clicks += 1
        self.assertIn('29.90 EUR', self.text())
        self.widget('规格').value = '小份'
        self.widget('数量（1—99）').value = 3
        self.widget('保存规格、配料和数量').clicks += 1
        self.assertIn('27.00 EUR', self.text())
        self.widget('删除所选商品').clicks += 1
        self.assertIn('总计：0.00 EUR', self.text())
        self.widget('添加为新商品').clicks += 1
        self.widget('新对话（同时清空购物车）').clicks += 1
        self.assertIn('总计：0.00 EUR', self.text())

    async def test_empty_and_explicit_fake_send(self):
        self.assertNotIn('已收到消息', self.text())
        await self.click_send()
        self.assertTrue(any(p.visible and '请输入消息' in str(p.object) for p in self.app.select(pn.pane.Alert)))
        self.widget('消息').value = '你好'
        await self.click_send()
        self.assertIn('【假回复 / 未调用模型】', self.text())
        self.assertEqual(self.widget('消息').value, '')
        self.assertFalse(self.widget('发送').disabled)

    async def test_wait_and_failure(self):
        from orderbot.model import FakeBackend, ModelError
        import threading
        started, release = threading.Event(), threading.Event()
        def broken(_, messages):
            started.set()
            release.wait(3)
            raise ModelError('模拟请求失败')
        self.widget('消息').value = '保留重试内容'
        with patch.object(FakeBackend, 'reply', broken):
            task = asyncio.create_task(self.click_send())
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(started.is_set())
                self.assertTrue(self.widget('发送').disabled)
                self.assertTrue(self.widget('新对话（同时清空购物车）').disabled)
                self.assertIn('发送中', self.text())
            finally:
                release.set()
                await task
        self.assertFalse(self.widget('发送').disabled)
        self.assertEqual(self.widget('消息').value, '保留重试内容')
        self.assertTrue(any(p.visible and '模拟请求失败' in str(p.object) for p in self.app.select(pn.pane.Alert)))

    async def test_session_isolation(self):
        from orderbot.ui import create_app
        self.widget('添加为新商品').clicks += 1
        with patch.dict(os.environ, {'ORDERBOT_MODE': 'fake'}):
            other = create_app()
        text = '\n'.join(str(p.object) for p in other.select(pn.pane.Str))
        self.assertIn('购物车为空', text)

    async def test_demo_tools_and_controls_share_state(self):
        self.widget('运行模式（切换会清空聊天和购物车）').value = 'demo'
        self.widget('消息').value = '添加一份大份汉堡，加芝士'
        await self.click_send()
        self.assertIn('14.95 EUR', self.text())
        self.assertIn('add_item', self.text())
        self.widget('数量（1—99）').value = 2
        self.widget('保存规格、配料和数量').clicks += 1
        self.widget('消息').value = '查看购物车'
        await self.click_send()
        self.assertIn('29.90 EUR', self.text())
        self.assertIn('get_cart', self.text())
        self.widget('消息').value = '删除第1行'
        await self.click_send()
        self.assertIn('总计：0.00 EUR', self.text())
        self.assertIn('remove_item', self.text())
        self.widget('新对话（同时清空购物车）').clicks += 1
        self.assertNotIn('remove_item', self.text())
        self.assertIn('图状态已清空', self.text())
        self.widget('消息').value = '添加一份小份汉堡，不加配料'
        await self.click_send()
        self.assertIn('7.00 EUR', self.text())
        self.widget('运行模式（切换会清空聊天和购物车）').value = 'fake'
        self.assertIn('总计：0.00 EUR', self.text())

    async def test_demo_isolation_and_missing_size(self):
        self.widget('运行模式（切换会清空聊天和购物车）').value = 'demo'
        self.widget('消息').value = '添加一份汉堡，不加配料'
        await self.click_send()
        self.assertIn('请明确', self.text())
        self.assertIn('总计：0.00 EUR', self.text())
        self.assertIn('未完成', self.text())
        self.widget('添加为新商品').clicks += 1
        from orderbot.ui import create_app
        with patch.dict(os.environ, {'ORDERBOT_MODE': 'demo'}):
            other = create_app()
        self.assertTrue(any('购物车为空' in str(p.object) for p in other.select(pn.pane.Str)))


    async def test_live_text_input_does_not_need_blur(self):
        self.widget('消息').value_input = '未失焦就发送'
        await self.click_send()
        self.assertIn('你：未失焦就发送', self.text())
        self.assertEqual(self.widget('消息').value_input, '')

    async def test_real_missing_config_keeps_real_mode_and_shows_error(self):
        from orderbot.ui import create_app
        with patch.dict(os.environ, {'ORDERBOT_MODE': 'real', 'ORDERBOT_PROVIDER': 'openai', 'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''}), patch('openai.OpenAI', side_effect=AssertionError('不能请求')):
            self.app = create_app()
            self.widget('消息').value = '查看菜单'
            await self.click_send()
        self.assertEqual(self.widget('运行模式（切换会清空聊天和购物车）').value, 'real')
        self.assertTrue(any(p.visible and '缺少 OPENAI_API_KEY' in str(p.object) for p in self.app.select(pn.pane.Alert)))
        self.assertIn('真实模型模式', self.text())
        self.assertIn('请求尝试：0/', self.text())
        self.assertNotIn('规则替身，未调用真实模型', self.text())

    async def test_zhipu_real_placeholder_keeps_provider_and_no_network(self):
        from orderbot.ui import create_app
        with patch.dict(os.environ, {'ORDERBOT_MODE': 'real', 'ORDERBOT_PROVIDER': 'zhipu', 'ZHIPU_API_KEY': 'PASTE_ZHIPU_API_KEY_HERE'}), patch('openai.OpenAI', side_effect=AssertionError('不能请求')):
            self.app = create_app()
            self.widget('消息').value = '查看菜单'
            await self.click_send()
        self.assertEqual(self.widget('运行模式（切换会清空聊天和购物车）').value, 'real')
        self.assertTrue(any('glm-4.7-flash' in str(p.object) for p in self.app.select(pn.pane.Alert)))
        self.assertIn('ZHIPU_API_KEY', self.text())
        self.assertIn('请求尝试：0/', self.text())
