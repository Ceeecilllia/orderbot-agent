import unittest
from orderbot.cart import Cart
from orderbot.menu import MENU, money

class CartTests(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()

    def test_all_sizes(self):
        for pid, product in MENU.items():
            for size, price in product.prices.items():
                with self.subTest(product=pid, size=size):
                    item = self.cart.add_item(pid, size)
                    self.assertEqual(item.unit_cents, price)
        self.assertEqual(money(1295), '12.95 EUR')

    def test_toppings_and_independent_lines(self):
        a = self.cart.add_item('burger', '大份', ['cheese', 'mushrooms'], 2)
        b = self.cart.add_item('burger', '小份')
        self.assertNotEqual(a.line_id, b.line_id)
        self.assertEqual(a.subtotal_cents, 3290)
        self.assertEqual(self.cart.total_cents, 3990)

    def test_update_remove_clear(self):
        a = self.cart.add_item('burger', '大份', ['cheese'], 2)
        b = self.cart.update_item(a.line_id, size='小份', toppings=['peppers'], quantity=3)
        self.assertEqual(b.line_id, a.line_id)
        self.assertEqual(self.cart.total_cents, 2400)
        self.cart.remove_item(a.line_id)
        self.assertEqual(self.cart.total_cents, 0)
        self.cart.add_item('salad', '标准份')
        self.cart.clear()
        self.assertEqual(self.cart.items, ())

    def test_invalid_add_is_atomic(self):
        invalid = [
            ('missing', '大份', [], 1), ('burger', '超大', [], 1),
            ('burger', '大份', ['unknown'], 1), ('burger', '大份', ['cheese', 'cheese'], 1),
            ('coke', '大杯', ['cheese'], 1), ('burger', '大份', None, 1),
        ] + [('burger', '大份', [], q) for q in (0, -1, 100, 1.5, '2', True, None)]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.cart.add_item(*args)
        self.assertEqual(self.cart.items, ())

    def test_invalid_update_preserves_cart(self):
        a = self.cart.add_item('fries', '小份', quantity=2)
        with self.assertRaises(ValueError):
            self.cart.update_item(a.line_id, size='大份', toppings=[], quantity=0)
        self.assertEqual(self.cart.items, (a,))
        with self.assertRaises(ValueError):
            self.cart.remove_item('missing')
        with self.assertRaises(ValueError):
            self.cart.update_item('missing', size='大份', toppings=[], quantity=1)

    def test_every_topping_and_quantity_bounds(self):
        from orderbot.menu import TOPPINGS
        for tid, (_, price) in TOPPINGS.items():
            item = self.cart.add_item('sandwich', '中份', [tid], 99)
            self.assertEqual(item.subtotal_cents, (975 + price) * 99)
