
import json

from tools.notifications import (
    notify_owner,
    NOTIFICATION_FILE,
)


print("\nTEST 1: CUSTOMER COMPLAINT")
print(
    notify_owner(
        category="complaint",
        message="Customer says their jollof rice arrived cold.",
        order_id=1,
        priority="high",
    )
)


print("\nTEST 2: REFUND REQUEST")
print(
    notify_owner(
        category="refund_request",
        message="Customer requests a refund for a missing item.",
        order_id=1,
    )
)


print("\nTEST 3: SPECIAL REQUEST")
print(
    notify_owner(
        category="special_request",
        message="Customer requests catering for 100 guests.",
        priority="medium",
    )
)


print("\nTEST 4: INVALID CATEGORY")
print(
    notify_owner(
        category="random_category",
        message="Testing an invalid category.",
    )
)


print("\nTEST 5: SAVED NOTIFICATIONS")

if NOTIFICATION_FILE.exists():
    with open(NOTIFICATION_FILE, "r", encoding="utf-8") as file:
        notifications = json.load(file)

    print("Total notifications:", len(notifications))
    print("Most recent notification:")
    print(json.dumps(notifications[-1], indent=4))
else:
    print("Notification file was not created.")
