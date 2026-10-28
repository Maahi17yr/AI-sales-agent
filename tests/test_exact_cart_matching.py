"""Regression tests for exact-name catalogue matching (no Strands dependency).

Run: uv run python -m unittest discover -s tests -p "test_exact_cart_matching.py" -v
"""
import unittest

from tools.product_matching import match_exact_orderable_product


PRODUCTS = [
    {"id": 13, "name": "Ogbono Soup", "price": 4000},
    {"id": 14, "name": "Afang Soup", "price": 4500},
    {"id": 17, "name": "Eba", "price": 800},
]


class ExactCartMatchingTests(unittest.TestCase):
    def resolve(self, name, products=None):
        return match_exact_orderable_product(name, PRODUCTS if products is None else products)

    def test_afang_never_resolves_to_ogbono(self):
        result = self.resolve("Afang Soup")
        self.assertTrue(result["success"])
        self.assertEqual(result["product"]["id"], 14)
        self.assertNotEqual(result["product"]["name"], "Ogbono Soup")

    def test_eba_resolves_to_eba(self):
        result = self.resolve("Eba")
        self.assertTrue(result["success"])
        self.assertEqual(result["product"]["id"], 17)

    def test_case_and_whitespace_are_accepted(self):
        result = self.resolve("  afang   SOUP ")
        self.assertTrue(result["success"])
        self.assertEqual(result["product"]["name"], "Afang Soup")

    def test_partial_name_is_not_silently_substituted(self):
        self.assertFalse(self.resolve("Soup")["success"])

    def test_unavailable_dish_is_rejected(self):
        self.assertFalse(self.resolve("Banga Soup")["success"])

    def test_empty_name_is_rejected(self):
        for name in ("", "   ", None, 14):
            with self.subTest(name=name):
                self.assertFalse(self.resolve(name)["success"])

    def test_duplicate_exact_names_are_rejected(self):
        self.assertFalse(self.resolve("Afang Soup", [
            {"id": 14, "name": "Afang Soup"},
            {"id": 99, "name": "afang soup"},
        ])["success"])


if __name__ == "__main__":
    unittest.main()
