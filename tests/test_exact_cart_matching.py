"""Regression tests for exact-name product selection in cart tools.

Run: python -m unittest discover -s tests -p 'test_exact_cart_matching.py' -v
"""
import unittest
from unittest.mock import patch

from tools.cart import resolve_orderable_product_by_name


PRODUCTS = [
    {"id": 13, "name": "Ogbono Soup", "price": 4000},
    {"id": 14, "name": "Afang Soup", "price": 4500},
    {"id": 17, "name": "Eba", "price": 800},
]


class ExactCartMatchingTests(unittest.TestCase):
    def search_products(self, query):
        return [p for p in PRODUCTS if query.casefold() in p["name"].casefold()]

    def setUp(self):
        patcher = patch("tools.cart.repository.get_orderable_products", side_effect=self.search_products)
        self.addCleanup(patcher.stop)
        self.search = patcher.start()

    def test_afang_never_resolves_to_ogbono(self):
        result = resolve_orderable_product_by_name("Afang Soup")
        self.assertTrue(result["success"])
        self.assertEqual(result["product"]["id"], 14)
        self.assertNotEqual(result["product"]["name"], "Ogbono Soup")

    def test_eba_resolves_to_eba(self):
        result = resolve_orderable_product_by_name("Eba")
        self.assertTrue(result["success"])
        self.assertEqual(result["product"]["id"], 17)

    def test_case_and_whitespace_are_accepted(self):
        result = resolve_orderable_product_by_name("  afang   SOUP ")
        self.assertTrue(result["success"])
        self.assertEqual(result["product"]["name"], "Afang Soup")

    def test_partial_name_is_not_silently_substituted(self):
        result = resolve_orderable_product_by_name("Soup")
        self.assertFalse(result["success"])

    def test_unavailable_dish_is_rejected(self):
        result = resolve_orderable_product_by_name("Banga Soup")
        self.assertFalse(result["success"])

    def test_empty_name_is_rejected(self):
        for name in ("", "   ", None, 14):
            with self.subTest(name=name):
                self.assertFalse(resolve_orderable_product_by_name(name)["success"])

    def test_duplicate_exact_names_are_rejected(self):
        with patch("tools.cart.repository.get_orderable_products", return_value=[
            {"id": 14, "name": "Afang Soup"},
            {"id": 99, "name": "afang soup"},
        ]):
            self.assertFalse(resolve_orderable_product_by_name("Afang Soup")["success"])


if __name__ == "__main__":
    unittest.main()
