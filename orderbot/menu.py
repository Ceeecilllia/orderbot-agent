"""演示菜单。金额统一用欧分；规格含义是演示设定，见 README。"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

CURRENCY = 'EUR'

@dataclass(frozen=True)
class Product:
    name: str
    prices: Mapping[str, int]

MENU = MappingProxyType({
    'burger': Product('汉堡', MappingProxyType({'大份': 1295, '中份': 1000, '小份': 700})),
    'frankfurt': Product('法兰克福香肠', MappingProxyType({'大份': 1095, '中份': 925, '小份': 650})),
    'sandwich': Product('三明治', MappingProxyType({'大份': 1195, '中份': 975, '小份': 675})),
    'fries': Product('薯条', MappingProxyType({'大份': 450, '小份': 350})),
    'salad': Product('沙拉', MappingProxyType({'标准份': 725})),
    'coke': Product('可乐', MappingProxyType({'大杯': 300, '中杯': 200, '小杯': 100})),
    'sprite': Product('雪碧', MappingProxyType({'大杯': 300, '中杯': 200, '小杯': 100})),
    'vichy': Product('Vichy Catalan', MappingProxyType({'标准份': 500})),
})
TOPPINGS = MappingProxyType({
    'cheese': ('额外芝士', 200), 'mushrooms': ('蘑菇', 150),
    'martra': ('martra sausage（原文名称）', 300), 'bacon': ('加拿大培根', 350),
    'romesco': ('Romesco 酱', 150), 'peppers': ('甜椒', 100),
})
TOPPING_PRODUCTS = frozenset({'burger', 'frankfurt', 'sandwich'})

def money(cents):
    return f'{cents // 100}.{cents % 100:02d} {CURRENCY}'

def menu_text():
    return '\n'.join(
        f'{pid} {product.name}: ' + '、'.join(f'{size} {money(price)}' for size, price in product.prices.items())
        for pid, product in MENU.items()
    ) + '\n配料（每份加价）: ' + '、'.join(f'{name} {money(price)}' for name, price in TOPPINGS.values())
