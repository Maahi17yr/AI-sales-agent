
from services.cart import CartService
from tools.orders import create_order, get_order_status

session_id = "orders_tool_test_001"
cart = CartService(session_id)

cart.clear_cart()
cart.add_item(product_id=1, quantity=2)

print("\nTEST 1: PREPARE ORDER")

result = create_order(
    session_id=session_id,
    customer_name="Test Customer",
    phone="08000000001",
    delivery_address="12 Test Street, Ikeja",
    delivery_area="Ikeja",
)

print(result)

print("\nTEST 2: CHECK EXISTING ORDER")

# Change this to an order ID created in your earlier test.
print(get_order_status(order_id=1))

print("\nTEST 3: CART STILL EXISTS")

print(cart.view_cart())

cart.clear_cart()
