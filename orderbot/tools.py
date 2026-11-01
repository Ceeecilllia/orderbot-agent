"""工具边界：参数校验 → 既有业务函数 → 可核对的结构化结果。"""
from dataclasses import asdict
from typing import Optional
from pydantic import BaseModel, ConfigDict, ValidationError
from .menu import MENU, TOPPINGS, TOPPING_PRODUCTS, CURRENCY, money


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class MenuArgs(Arguments):
    product_id: Optional[str] = None


class EmptyArgs(Arguments):
    pass


class AddArgs(Arguments):
    product_id: Optional[str] = None
    size: Optional[str] = None
    quantity: Optional[int] = None
    toppings: Optional[list[str]] = None


class UpdateArgs(Arguments):
    line_id: Optional[str] = None
    size: Optional[str] = None
    quantity: Optional[int] = None
    toppings: Optional[list[str]] = None


class RemoveArgs(Arguments):
    line_id: Optional[str] = None


ARGUMENTS = {
    'query_menu': MenuArgs, 'get_cart': EmptyArgs, 'add_item': AddArgs,
    'update_item': UpdateArgs, 'remove_item': RemoveArgs,
}
DESCRIPTIONS = {
    'query_menu': '查询菜单，product_id 为 null 时返回全部菜单，含规格、配料编号及程序价格。',
    'get_cart': '查看当前购物车和完整行编号。修改/删除对象有歧义时先查询并询问，不能随意选一行。',
    'add_item': '新增独立商品行。明确 product_id、size、quantity；无配料传 []。不清楚的字段传 null，程序会要求澄清，不猜规格。不能传价格。',
    'update_item': '修改一行，line_id 必须明确。size、quantity、toppings 为 null 时保留原值；[] 表示去除全部配料。不能猜修改对象或传价格。',
    'remove_item': '删除明确 line_id 的商品。对象不明确传 null，先询问用户。',
}


def tool_definitions():
    definitions = []
    for name, args in ARGUMENTS.items():
        schema = args.model_json_schema()
        # Responses 严格模式：所有字段 required，可空字段通过 null 表达。
        schema['required'] = list(schema.get('properties', {}))
        for field in schema.get('properties', {}).values():
            field.pop('default', None)
        definitions.append({'type': 'function', 'name': name, 'description': DESCRIPTIONS[name],
                            'strict': True, 'parameters': schema})
    return definitions


def cart_snapshot(cart):
    return {'currency': CURRENCY, 'items': [
        {**asdict(i), 'toppings': list(i.toppings), 'name': MENU[i.product_id].name,
         'subtotal_cents': i.subtotal_cents} for i in cart.items
    ], 'total_cents': cart.total_cents}


class BusinessTools:
    def __init__(self, cart):
        self.cart = cart

    def execute(self, name, arguments):
        """任何错误都作为 ok=false 返回；不把原始异常或账户配置显示出来。"""
        if not isinstance(name, str) or name not in ARGUMENTS:
            return self.failure('unknown_tool', '未知工具，未执行操作。')
        try:
            args = ARGUMENTS[name].model_validate(arguments)
            return getattr(self, name)(**args.model_dump())
        except ValidationError:
            return self.failure('invalid_arguments', '参数无效：请检查字段名和类型；数量必须为整数，且不能传入价格。')
        except ValueError as exc:
            return self.failure('invalid_input', str(exc))
        except Exception:
            return self.failure('tool_exception', '工具执行异常，未确认操作完成。请先查看购物车，勿直接重复添加。')

    def failure(self, code, message):
        return {'ok': False, 'code': code, 'message': message, 'cart': cart_snapshot(self.cart)}

    def success(self, message, **data):
        return {'ok': True, 'message': message, **data, 'cart': cart_snapshot(self.cart)}

    def query_menu(self, product_id=None):
        if product_id is not None and product_id not in MENU:
            return self.failure('invalid_input', '菜单中没有这个商品编号。')
        products = {pid: {'name': p.name, 'prices_cents': dict(p.prices),
                          'allows_toppings': pid in TOPPING_PRODUCTS}
                    for pid, p in MENU.items() if product_id is None or pid == product_id}
        return self.success('已查询菜单。', menu=products,
                            toppings={tid: {'name': name, 'price_cents': price}
                                      for tid, (name, price) in TOPPINGS.items()}, currency=CURRENCY)

    def get_cart(self):
        return self.success('已查看购物车。')

    def add_item(self, product_id=None, size=None, quantity=None, toppings=None):
        if product_id is None or size is None or quantity is None or toppings is None:
            return self.failure('needs_clarification',
                                '请明确商品、规格、数量和配料（不加配料可说“不加配料”）。请勿猜规格。')
        item = self.cart.add_item(product_id, size, toppings, quantity)
        return self.success('已添加商品。', line_id=item.line_id)

    def update_item(self, line_id=None, size=None, quantity=None, toppings=None):
        if not line_id:
            return self.failure('needs_clarification', '请明确要修改哪一行商品（行编号或完整规格及配料）。')
        old = next((i for i in self.cart.items if i.line_id == line_id), None)
        if old is None:
            return self.failure('not_found', '购物车中没有这条商品。')
        if size is None and quantity is None and toppings is None:
            return self.failure('needs_clarification', '请说明要修改的规格、数量或配料。')
        self.cart.update_item(line_id, size=old.size if size is None else size,
                              toppings=old.toppings if toppings is None else toppings,
                              quantity=old.quantity if quantity is None else quantity)
        return self.success('已修改商品。', line_id=line_id)

    def remove_item(self, line_id=None):
        if not line_id:
            return self.failure('needs_clarification', '请明确要删除哪一行商品（行编号或完整规格及配料）。')
        self.cart.remove_item(line_id)
        return self.success('已删除商品。', line_id=line_id)


def result_text(name, result):
    """用于工具记录与最终回执，金额只从业务结果格式化。"""
    if not result['ok']:
        text = f"{result['code']}：{result['message']}"
    else:
        text = result['message']
    if name == 'query_menu' and result['ok']:
        text += '\n' + '\n'.join(
            f"{pid} / {p['name']}：" + '、'.join(f'{s} {money(v)}' for s, v in p['prices_cents'].items())
            for pid, p in result['menu'].items())
        text += '\n配料：' + '、'.join(f"{tid}/{p['name']} +{money(p['price_cents'])}" for tid, p in result['toppings'].items())
    if name == 'get_cart' or not result['ok']:
        for i in result['cart']['items']:
            text += f"\n行 {i['line_id']}：{i['name']} / {i['size']} / 配料 {i['toppings']} × {i['quantity']}"
    if result.get('line_id'):
        text += '\n行编号：' + result['line_id']
    return text + '\n当前总计：' + money(result['cart']['total_cents'])
