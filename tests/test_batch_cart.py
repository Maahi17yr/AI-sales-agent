"""Regression tests for the single-call, all-or-nothing basket workflow."""
import unittest
from unittest.mock import patch

from services.cart import CartService


PRODUCTS = {
    1: {"id": 1, "name": "Eba", "available": 1, "price": 800},
    2: {"id": 2, "name": "Afang Soup", "available": 1, "price": 4500},
}


class InMemoryCart(CartService):
    def __init__(self, stock_error=None):
        super().__init__("verified-test-session")
        self.items = []
        self.saves = 0
        self.stock_error = stock_error

    def get_cart(self):
        return self.items

    def _save_cart(self, items):
        self.items = items
        self.saves += 1

    def _check_all_stock(self, items):
        return self.stock_error

    def view_cart(self):
        return {
            "items": [
                {"product_id": line["product_id"],
                 "name": PRODUCTS[line["product_id"]]["name"],
                 "quantity": line["quantity"]}
                for line in self.items
            ],
            "subtotal": sum(
                PRODUCTS[line["product_id"]]["price"] * line["quantity"]
                for line in self.items
            ),
            "currency": "NGN",
        }


class BatchCartTests(unittest.TestCase):
    def setUp(self):
        self.products = patch(
            "services.cart.repository.get_product",
            side_effect=lambda product_id: PRODUCTS.get(product_id),
        )
        self.products.start()
        self.addCleanup(self.products.stop)

    def test_adds_two_meals_and_saves_only_once(self):
        cart = InMemoryCart()
        result = cart.add_items([
            {"product_id": 1, "quantity": 1},
            {"product_id": 2, "quantity": 1},
        ])
        self.assertTrue(result["success"])
        self.assertEqual(cart.saves, 1)
        self.assertEqual(result["cart"]["subtotal"], 5300)
        self.assertEqual([x["name"] for x in result["cart"]["items"]],
                         ["Eba", "Afang Soup"])

    def test_failed_stock_check_does_not_partially_save(self):
        cart = InMemoryCart(stock_error="Not enough meal stock.")
        result = cart.add_items([
            {"product_id": 1, "quantity": 1},
            {"product_id": 2, "quantity": 1},
        ])
        self.assertFalse(result["success"])
        self.assertEqual(cart.saves, 0)
        self.assertEqual(cart.items, [])

    def test_invalid_second_product_does_not_change_existing_cart(self):
        cart = InMemoryCart()
        cart.items = [{"product_id": 1, "variant": "standard",
                       "quantity": 2, "modifications": []}]
        result = cart.add_items([
            {"product_id": 1, "quantity": 1},
            {"product_id": 99, "quantity": 1},
        ])
        self.assertFalse(result["success"])
        self.assertEqual(cart.saves, 0)
        self.assertEqual(cart.items[0]["quantity"], 2)

    def test_duplicate_product_lines_are_combined(self):
        cart = InMemoryCart()
        result = cart.add_items([
            {"product_id": 1, "quantity": 1},
            {"product_id": 1, "quantity": 2},
        ])
        self.assertTrue(result["success"])
        self.assertEqual(len(result["cart"]["items"]), 1)
        self.assertEqual(result["cart"]["items"][0]["quantity"], 3)

    def test_rejects_bad_quantities_without_saving(self):
        cart = InMemoryCart()
        result = cart.add_items([{"product_id": 1, "quantity": True}])
        self.assertFalse(result["success"])
        self.assertEqual(cart.saves, 0)


if __name__ == "__main__":
    unittest.main()
