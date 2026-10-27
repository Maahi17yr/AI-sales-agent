
from services.cart import CartService
from services.checkout import CheckoutService

session_id = "checkout_test_001"

cart = CartService(session_id)
checkout = CheckoutService(session_id)

cart.clear_cart()
cart.add_item(product_id=1, quantity=2)

print("\n1. PREPARE CHECKOUT")

prepared = checkout.prepare_checkout(
    customer_name="Test Customer",
    phone="08000000002",
    delivery_address="20 Example Street, Ikeja",
    delivery_area="Ikeja",
)

print(prepared)

print("\n2. TRY WITHOUT APPROVAL")
print(checkout.confirm_checkout(customer_approved=False))

print("\n3. CUSTOMER APPROVES")

# Simulates an explicit confirmation in our terminal UI.
# In the real application, main.py will obtain this
# from the customer's Yes/No response.
if prepared["success"]:
    answer = input("Confirm this order? (yes/no): ")
    approved = answer.strip().lower() == "yes"

    result = checkout.confirm_checkout(
        customer_approved=approved
    )

    print(result)

print("\n4. CART AFTER CHECKOUT")
print(cart.view_cart())
