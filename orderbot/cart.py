"""纯 Python 购物车，不依赖 Panel 或模型。"""
from dataclasses import dataclass
from uuid import uuid4
from .menu import MENU, TOPPINGS, TOPPING_PRODUCTS

@dataclass(frozen=True)
class CartItem:
    line_id: str
    product_id: str
    size: str
    toppings: tuple
    quantity: int
    unit_cents: int

    @property
    def subtotal_cents(self):
        return self.unit_cents * self.quantity


def validate(product_id, size, toppings, quantity):
    if not isinstance(product_id, str) or product_id not in MENU:
        raise ValueError('请选择有效商品。')
    if not isinstance(size, str) or size not in MENU[product_id].prices:
        raise ValueError('该商品没有这个规格。')
    if type(quantity) is not int or not 1 <= quantity <= 99:
        raise ValueError('数量必须是 1 到 99 的整数。')
    if not isinstance(toppings, (list, tuple)) or any(not isinstance(t, str) or t not in TOPPINGS for t in toppings):
        raise ValueError('配料无效。')
    if len(set(toppings)) != len(toppings):
        raise ValueError('同一种配料每份只能添加一次。')
    if toppings and product_id not in TOPPING_PRODUCTS:
        raise ValueError('演示版仅汉堡、香肠和三明治支持加配料。')
    return tuple(sorted(toppings))


class Cart:
    def __init__(self):
        self._items = {}

    @property
    def items(self):
        return tuple(self._items.values())

    @property
    def total_cents(self):
        return sum(item.subtotal_cents for item in self.items)

    def _make_item(self, line_id, product_id, size, toppings, quantity):
        toppings = validate(product_id, size, toppings, quantity)
        price = MENU[product_id].prices[size] + sum(TOPPINGS[t][1] for t in toppings)
        return CartItem(line_id, product_id, size, toppings, quantity, price)

    def add_item(self, product_id, size, toppings=(), quantity=1):
        line_id = uuid4().hex
        item = self._make_item(line_id, product_id, size, toppings, quantity)
        self._items[line_id] = item
        return item

    def update_item(self, line_id, *, size, toppings, quantity):
        old = self._get(line_id)
        item = self._make_item(line_id, old.product_id, size, toppings, quantity)
        self._items[line_id] = item
        return item

    def _get(self, line_id):
        if not isinstance(line_id, str) or line_id not in self._items:
            raise ValueError('购物车中没有这条商品。')
        return self._items[line_id]

    def remove_item(self, line_id):
        self._get(line_id)
        del self._items[line_id]

    def clear(self):
        self._items.clear()
