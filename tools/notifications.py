
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from strands import tool


BASE_DIR = Path(__file__).resolve().parent.parent
NOTIFICATION_FILE = BASE_DIR / "database" / "owner_notifications.json"

ALLOWED_CATEGORIES = {
    "complaint",
    "refund_request",
    "order_issue",
    "special_request",
    "other",
}

ALLOWED_PRIORITIES = {"low", "medium", "high"}


@tool
def notify_owner(
    category: str,
    message: str,
    order_id: int = 0,
    priority: str = "medium",
) -> dict:
    """
    Notify the business owner about an issue requiring attention.

    Use for customer complaints, refund requests, order
    problems, or special requests that require human approval.

    This tool only records a simulated notification.
    It does not contact the owner directly.

    Args:
        category: complaint, refund_request, order_issue,
                  special_request, or other.
        message: A brief description of the issue.
        order_id: Related order ID, if available. Use 0 otherwise.
        priority: low, medium, or high.
    """

    category = category.strip().lower()
    priority = priority.strip().lower()
    message = message.strip()

    if category not in ALLOWED_CATEGORIES:
        return {
            "success": False,
            "message": "Invalid notification category.",
        }

    if priority not in ALLOWED_PRIORITIES:
        return {
            "success": False,
            "message": "Invalid notification priority.",
        }

    if not message:
        return {
            "success": False,
            "message": "A notification message is required.",
        }

    if type(order_id) is not int or order_id < 0:
        return {
            "success": False,
            "message": "Invalid order ID.",
        }

    notification = {
        "notification_id": str(uuid.uuid4()),
        "category": category,
        "priority": priority,
        "order_id": order_id if order_id else None,
        "message": message,
        "status": "unread",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    NOTIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        if NOTIFICATION_FILE.exists():
            with open(NOTIFICATION_FILE, "r", encoding="utf-8") as file:
                notifications = json.load(file)
        else:
            notifications = []

        notifications.append(notification)

        with open(NOTIFICATION_FILE, "w", encoding="utf-8") as file:
            json.dump(notifications, file, indent=4)

    except (OSError, json.JSONDecodeError) as error:
        return {
            "success": False,
            "message": f"Unable to save notification: {error}",
        }

    return {
        "success": True,
        "notification_id": notification["notification_id"],
        "category": category,
        "priority": priority,
        "message": "Owner notification recorded successfully.",
        "owner_contacted": False,
    }
