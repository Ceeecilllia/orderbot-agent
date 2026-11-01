"""Panel 界面；每个浏览器会话创建自己的聊天记录和购物车。"""
import asyncio
import os
import panel as pn
from .cart import Cart
from .menu import MENU, TOPPINGS, TOPPING_PRODUCTS, money, menu_text
from .model import ChatSession, FakeBackend, OpenAIBackend, ModelError
from .agent import AgentSession
from .demo_model import DemoBackend
from .tools import result_text
from .config import Settings
from .zhipu import ZhipuBackend


def text_pane(text):
    return pn.pane.Str(text, sizing_mode="stretch_width",
                       stylesheets=["pre { white-space: pre-wrap; overflow-wrap: anywhere; }"])


def create_app():
    pn.extension()
    settings = Settings.from_env()
    mode = settings.mode
    if mode not in ('fake', 'demo', 'real'):
        raise ValueError('ORDERBOT_MODE 只能是 fake、demo 或 real。')
    cart = Cart()
    def make_session(value):
        if value == 'fake':
            return ChatSession(FakeBackend())
        backend = DemoBackend() if value == 'demo' else (ZhipuBackend(settings) if settings.provider == 'zhipu' else OpenAIBackend(settings))
        return AgentSession(backend, cart,
                            max_tool_rounds=settings.max_tool_rounds, max_model_calls=settings.max_model_calls)

    chat = make_session(mode)
    labels = {
        'fake': '假回复模式：不调用模型，不运行 Agent，不修改购物车。',
        'demo': '模拟模式 / 模拟 Agent：本地规则替身驱动 LangGraph 和真实业务工具；未调用真实模型。',
        'real': '真实模型模式 / 真实 Agent：点击发送会请求模型并可能收费；本轮未验证真实模型。',
    }
    if settings.provider == 'zhipu':
        labels['real'] = '真实模型模式 / 智谱 glm-4.7-flash（免费限定）：固定官方服务；限流请稍后重试，付费提示即停止。'
    mode_select = pn.widgets.Select(name='运行模式（切换会清空聊天和购物车）',
        options={'假回复（不执行工具）': 'fake', '模拟 Agent（无 API）': 'demo', '真实 Agent（API 可能收费）': 'real'}, value=mode)
    banner = pn.pane.Alert(labels[mode], alert_type='warning')
    messages = pn.Column(text_pane('欢迎！模拟/真实 Agent 可调用工具点餐；右侧控件与聊天共用购物车。'), sizing_mode='stretch_width')
    tool_log = text_pane('本轮尚未调用工具。')
    usage_log = text_pane('尚未请求真实模型；未提供用量。')
    prompt = pn.widgets.TextInput(name='消息', placeholder='请输入消息')
    send = pn.widgets.Button(name='发送', button_type='primary')
    reset = pn.widgets.Button(name='新对话（同时清空购物车）')
    status = text_pane('就绪')
    error = pn.pane.Alert('', alert_type='danger', visible=False)
    product = pn.widgets.Select(name='商品编号 / 名称', options={f'{pid} / {p.name}': pid for pid, p in MENU.items()})
    size = pn.widgets.Select(name='规格', options=list(MENU[product.value].prices))
    toppings = pn.widgets.MultiChoice(name='配料（每份加价）', options={f'{name} +{money(price)}': tid for tid, (name, price) in TOPPINGS.items()})
    quantity = pn.widgets.IntInput(name='数量（1—99）', value=1, start=1, end=99)
    add = pn.widgets.Button(name='添加为新商品', button_type='primary')
    selected = pn.widgets.Select(name='选择要修改 / 删除的商品', options={})
    update = pn.widgets.Button(name='保存规格、配料和数量')
    delete = pn.widgets.Button(name='删除所选商品', button_type='danger')
    cart_view = text_pane('')
    cart_error = pn.pane.Alert('', alert_type='danger', visible=False)
    refreshing = False

    def product_changed(*_):
        size.options = list(MENU[product.value].prices)
        size.value = size.options[0]
        toppings.value = []
        toppings.disabled = product.value not in TOPPING_PRODUCTS

    product.param.watch(product_changed, 'value')
    product_changed()

    def load_selected(*_):
        if refreshing:
            return
        item = next((i for i in cart.items if i.line_id == selected.value), None)
        if item:
            product.value = item.product_id
            size.value = item.size
            toppings.value = list(item.toppings)
            quantity.value = item.quantity

    selected.param.watch(load_selected, 'value')

    def refresh(preferred=None):
        nonlocal refreshing
        refreshing = True
        try:
            selected.options = {f'{n}. {MENU[i.product_id].name} / {i.size} × {i.quantity}': i.line_id for n, i in enumerate(cart.items, 1)}
            selected.value = preferred if preferred in selected.options.values() else next(iter(selected.options.values()), None)
            lines = []
            for n, item in enumerate(cart.items, 1):
                extras = '、'.join(TOPPINGS[t][0] for t in item.toppings) or '无'
                lines.append(f'{n}. {MENU[item.product_id].name} / {item.size}\n   行编号：{item.line_id}\n   配料：{extras}\n   {money(item.unit_cents)} × {item.quantity} = {money(item.subtotal_cents)}')
            cart_view.object = '\n\n'.join(lines or ['购物车为空']) + f'\n\n总计：{money(cart.total_cents)}'
            update.disabled = delete.disabled = not bool(cart.items)
        finally:
            refreshing = False
        load_selected()

    def change_cart(action):
        if send.disabled:
            return
        cart_error.visible = False
        try:
            if action == 'add':
                item = cart.add_item(product.value, size.value, toppings.value, quantity.value)
                refresh(item.line_id)
            elif action == 'update':
                old = next((i for i in cart.items if i.line_id == selected.value), None)
                if old is None or old.product_id != product.value:
                    raise ValueError('修改时请保持原商品类型；更换商品请删除后重新添加。')
                cart.update_item(selected.value, size=size.value, toppings=toppings.value, quantity=quantity.value)
                refresh(selected.value)
            else:
                cart.remove_item(selected.value)
                refresh()
        except ValueError as exc:
            cart_error.object = str(exc)
            cart_error.visible = True

    add.on_click(lambda _: change_cart('add'))
    update.on_click(lambda _: change_cart('update'))
    delete.on_click(lambda _: change_cart('delete'))

    async def send_message(_):
        if send.disabled:
            return
        error.visible = False
        text = prompt.value_input.strip()
        if not text:
            error.object = '请输入消息后再发送。'
            error.visible = True
            return
        send.disabled = reset.disabled = prompt.disabled = mode_select.disabled = True
        for widget in (product, size, toppings, quantity, add, selected, update, delete):
            widget.disabled = True
        send.loading = True
        status.object = '发送中…'
        try:
            reply = await asyncio.to_thread(chat.send, text)
            # Str 按文字显示用户和模型内容，避免当作 HTML 执行。
            messages.append(text_pane(f'你：{text}'))
            messages.append(text_pane(f'助手：{reply}'))
            prompt.value = ''
            prompt.value_input = ''
            if isinstance(chat, AgentSession) and chat.state['error']:
                error.object = chat.state['error']
                error.visible = True
        except (ModelError, ValueError) as exc:
            error.object = str(exc)
            error.visible = True
        except Exception:
            error.object = '处理消息失败，请重试。'
            error.visible = True
        finally:
            # 工具已执行但模型后续失败时也必须刷新，避免界面状态过时。
            refresh()
            if isinstance(chat, AgentSession):
                tool_log.object = '\n\n'.join(
                    f"{n}. {t['name']} — {'完成' if t['ok'] else '未完成'}\n{result_text(t['name'], t['result'])}"
                    for n, t in enumerate(chat.trace, 1)) or '本轮未调用工具。'
            else:
                tool_log.object = '假回复模式：未运行 Agent，未调用工具。'
            if isinstance(chat, AgentSession) and isinstance(chat.backend, OpenAIBackend):
                records = chat.backend.usage_records
                rows = []
                for record in records:
                    counts = ' / '.join(str(record[k]) if record[k] is not None else '未提供'
                                        for k in ('input_tokens', 'output_tokens', 'total_tokens'))
                    rows.append(f"{record['request']}. {record['status']} — 输入/输出/总 tokens：{counts}")
                usage_log.object = ('本会话模型请求尝试：' + str(len(records)) + '/' + str(settings.max_session_requests)
                                    + '（重试不单列；失败可能仍计费）\n' + '\n'.join(rows))
            else:
                usage_log.object = '模拟/假回复模式：没有真实模型请求，不能据此验证真实用量。'
            send.disabled = reset.disabled = prompt.disabled = mode_select.disabled = False
            for widget in (product, size, quantity, add, selected):
                widget.disabled = False
            toppings.disabled = product.value not in TOPPING_PRODUCTS
            send.loading = False
            status.object = '就绪'

    def new_conversation(_):
        chat.reset()
        cart.clear()
        messages.objects = [text_pane('新对话已开始，聊天、购物车与图状态已清空。')]
        tool_log.object = '本轮尚未调用工具。'
        usage_log.object = '新对话：本地用量记录已清空（服务端账单不会清空）。'
        prompt.value = ''
        prompt.value_input = ''
        error.visible = cart_error.visible = False
        quantity.value = 1
        toppings.value = []
        refresh()

    def mode_changed(event):
        nonlocal chat
        chat.reset()
        cart.clear()
        chat = make_session(event.new)
        banner.object = labels[event.new]
        new_conversation(None)

    mode_select.param.watch(mode_changed, 'value')

    # 普通事件回调，不使用初始化时会求值的 pn.bind。
    send.on_click(send_message)
    reset.on_click(new_conversation)
    refresh()
    return pn.Column(
        '# My Dear Frankfurt 点餐演示', mode_select, banner,
        pn.Row(
            pn.Column('## 聊天', messages, prompt, pn.Row(send, reset), status, error,
                      pn.Accordion(('本轮工具执行记录（不含内部推理）', tool_log),
                                   ('本会话真实模型用量', usage_log)), sizing_mode='stretch_width'),
            pn.Column('## 购物车（聊天工具与控件共用）', product, size, toppings, quantity, add,
                      selected, update, delete, cart_error, cart_view, sizing_mode='stretch_width'),
            sizing_mode='stretch_width',
        ),
        pn.Accordion(('菜单与价格（EUR）', text_pane(menu_text()))),
        sizing_mode='stretch_width',
    )
