
from services.checkout import CheckoutService
from database import repository

SESSION_ID = "naija-bites-local-dev-v1"

session = repository.get_session(SESSION_ID)

if not session:
    raise RuntimeError("Development session not found.")

print("Customer ID:", session["customer_id"])
print("Cart:", session["cart_data"])

result = CheckoutService(SESSION_ID).prepare_checkout(
    customer_name="John Dode",
    phone="08000000001",
    delivery_address="10 Example Street, Lekki Phase 1, Lagos",
    delivery_area="Lekki Phase 1",
)

print("\nCHECKOUT PREPARATION RESULT:")
print(result)

session = repository.get_session(SESSION_ID)
print("\nSAVED PENDING CHECKOUT:")
print(session["cart_data"].get("pending_checkout"))
