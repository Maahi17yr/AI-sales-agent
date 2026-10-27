
from services.cart import CartService
from services.order_service import OrderService
from database import repository

session_id = "order_test_001"

cart = CartService(session_id)
orders = OrderService(session_id)

# Prepare a fresh cart.
cart.clear_cart()
print("Add:", cart.add_item(1, 2))

# First attempt: no confirmation.
print("\nWITHOUT CONFIRMATION")
print(
    orders.create_order(
        customer_name="Test Customer",
        phone="08000000001",
        delivery_address="12 Test Street",
        delivery_area="Ikeja",
        confirmed=False,
    )
)

# Simulate a confirmation recorded by the application.
session = repository.get_session(session_id)

repository.save_session(
    session_id=session_id,
    cart_data=session["cart_data"],
    chat_history=session["chat_history"],
    customer_id=session["customer_id"],
    awaiting_confirmation=True,
)

# Second attempt: confirmed order.
print("\nWITH CONFIRMATION")
result = orders.create_order(
    customer_name="Test Customer",
    phone="08000000001",
    delivery_address="12 Test Street",
    delivery_area="Ikeja",
    confirmed=True,
)

print(result)

if result["success"]:
    print("\nORDER STATUS")
    print(repository.get_order_status(result["order_id"]))

print("\nCART AFTER ORDER")
print(cart.view_cart())
