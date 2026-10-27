
from services.cart import CartService
from tools.discount import apply_discount

session_id = "discount_tool_test_001"

cart = CartService(session_id)

# Start with an empty test cart.
cart.clear_cart()

print("\nTEST 1: Empty cart")
print(apply_discount(session_id))

print("\nTEST 2: Small order")
cart.add_item(product_id=1, quantity=2)
print(apply_discount(session_id))

print("\nTEST 3: Qualifying order")
cart.update_quantity(product_id=1, quantity=4)
print(apply_discount(session_id))

print("\nTEST 4: Larger order")
cart.update_quantity(product_id=1, quantity=7)
print(apply_discount(session_id))

# Clean up the test cart.
cart.clear_cart()
