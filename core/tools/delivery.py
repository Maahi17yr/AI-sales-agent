
from strands import tool
from data.database import repository


@tool
def get_delivery_fee(area: str) -> dict:
    """
    Check delivery availability, fee, and estimated time.

    Use this tool when a customer asks about delivery
    prices, supported locations, or delivery times.

    Args:
        area: Customer's delivery area, e.g. Ikeja or Yaba.

    Returns:
        Delivery availability, fee, and estimated hours.
    """

    if not area or not area.strip():
        return {
            "success": False,
            "message": "Please provide a delivery area.",
        }

    area = area.strip()

    zone = repository.get_delivery_zone(area)

    if not zone:
        return {
            "success": False,
            "available": False,
            "message": (
                f"We don't currently have a delivery rate "
                f"for {area}. Please provide a nearby "
                f"supported area or contact the vendor."
            ),
        }

    if not zone["available"]:
        return {
            "success": True,
            "available": False,
            "area": zone["area"],
            "message": (
                f"Delivery to {zone['area']} is "
                f"currently unavailable."
            ),
        }

    return {
        "success": True,
        "available": True,
        "area": zone["area"],
        "delivery_fee": zone["fee"],
        "currency": "NGN",
        "estimated_min_hours": zone["estimated_min_hours"],
        "estimated_max_hours": zone["estimated_max_hours"],
        "message": (
            f"Delivery to {zone['area']} costs "
            f"NGN {zone['fee']:,} and takes approximately "
            f"{zone['estimated_min_hours']}–"
            f"{zone['estimated_max_hours']} hours."
        ),
    }
