"""有限中文规则替身，不是语言模型。只用于本地演示与图流程验证。"""
import json
import re
from uuid import uuid4
from .menu import MENU

NUMBERS = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
PRODUCT_NAMES = {'burger': ('汉堡', 'burger'), 'frankfurt': ('香肠', 'frankfurt'),
                 'sandwich': ('三明治', 'sandwich'), 'fries': ('薯条', 'fries'),
                 'salad': ('沙拉', 'salad'), 'coke': ('可乐', 'coke'),
                 'sprite': ('雪碧', 'sprite'), 'vichy': ('vichy',)}
TOPPING_NAMES = {'cheese': '芝士', 'mushrooms': '蘑菇', 'martra': 'martra',
                 'bacon': '培根', 'romesco': 'romesco', 'peppers': '甜椒'}


def decision(text='', name=None, args=None):
    calls = []
    items = []
    if name:
        call = {'type': 'function_call', 'call_id': 'call_' + uuid4().hex,
                'name': name, 'arguments': json.dumps(args or {}, ensure_ascii=False)}
        calls = [call]
        items.append(call)
    elif text:
        items.append({'role': 'assistant', 'content': text})
    return {'text': text, 'calls': calls, 'items': items}


class DemoBackend:
    label = '【模拟 Agent / 规则替身，未调用真实模型】\n'

    def decide(self, messages, tools, cart):
        if messages[-1].get('type') == 'function_call_output':
            return decision('工具结果已收到。')
        text = messages[-1]['content'].lower().strip()
        if any(word in text for word in ('查看购物车', '我的购物车', '购物车里有什么')):
            return decision(name='get_cart')
        if any(word in text for word in ('菜单', '有什么', '多少钱')):
            return decision(name='query_menu', args={'product_id': None})
        products = [pid for pid, names in PRODUCT_NAMES.items() if any(n in text for n in names)]
        pid = products[0] if len(products) == 1 else None
        # 仅支持明确规格；标准份也需要用户明确说出。
        sizes = [s for s in ('大份', '中份', '小份', '大杯', '中杯', '小杯', '标准份') if s in text]
        size = sizes[0] if len(sizes) == 1 else None
        quantity = None
        number = re.search(r'(-?\d+(?:\.\d+)?|[一两二三四五六七八九十])\s*(?:份|杯|个)', text)
        if number:
            raw = number.group(1)
            quantity = NUMBERS.get(raw, int(raw) if re.fullmatch(r'-?\d+', raw) else raw)
        toppings = [tid for tid, name in TOPPING_NAMES.items() if name in text or tid in text]
        if '不加配料' in text:
            toppings = []
        elif any(w in text for w in ('不加', '不要', '去掉')):
            return decision('模拟替身暂不支持这种配料表达。请重新发送完整请求，明确列出要保留的配料或“不加配料”。')
        if any(word in text for word in ('分别', '其中', '然后', '再来')):
            return decision('模拟替身一次只支持一行商品，请分开发送完整请求。')
        adding = text.startswith(('添加', '加', '来', '我要', '要'))
        deleting = text.startswith(('删除', '移除', '删掉'))
        updating = any(w in text for w in ('改成', '改为', '修改'))
        if adding:
            return decision(name='add_item', args={'product_id': pid, 'size': size,
                                                  'quantity': quantity, 'toppings': toppings})
        if deleting or updating:
            line_id = None
            explicit = re.search(r'行(?:编号)?\s*[:：]?\s*([a-z0-9_-]+)', text)
            ordinal = re.search(r'第\s*(\d+|[一两二三四五六七八九十])\s*行', text)
            if explicit:
                line_id = explicit.group(1)
            elif ordinal:
                raw = ordinal.group(1)
                index = NUMBERS.get(raw, int(raw) if raw.isdigit() else 0) - 1
                if 0 <= index < len(cart['items']):
                    line_id = cart['items'][index]['line_id']
            elif pid:
                candidates = [i for i in cart['items'] if i['product_id'] == pid
                              and (not size or i['size'] == size)
                              and (not toppings or set(i['toppings']) == set(toppings))]
                if len(candidates) == 1:
                    line_id = candidates[0]['line_id']
            if deleting:
                return decision(name='remove_item', args={'line_id': line_id})
            # 模拟模式专注数量修改；避免把目标描述的规格/配料当作新配置。
            return decision(name='update_item', args={'line_id': line_id, 'size': None,
                                                      'quantity': quantity, 'toppings': None})
        return decision('模拟替身仅支持有限示例：查看菜单；添加一份大份汉堡，加芝士；查看购物车；把第1行改成3份；删除第1行。缺少信息时请重新发送完整请求。')
