
from tools.payment import create_payment_link
from database import repository

# Use an existing order created by your earlier tests.
order_id = 1

print("\nTEST 1: GENERATE PAYMENT LINK")
first = create_payment_link(order_id)
print(first)

print("\nTEST 2: REQUEST LINK AGAIN")
second = create_payment_link(order_id)
print(second)

print("\nTEST 3: CHECK FOR DUPLICATES")
if first["success"] and second["success"]:
    print(
        "Same reference:",
        first["payment_reference"] == second["payment_reference"]
    )

print("\nTEST 4: INVALID ORDER")
print(create_payment_link(999999))

print("\nTEST 5: ORDER PAYMENT STATUS")
print(repository.get_order_status(order_id))
