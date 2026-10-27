
from strands import tool
from services.discount_service import DiscountService


@tool
def apply_discount(session_id: str) -> dict:
    """
    Check whether the customer's current cart qualifies
    for an approved bulk-order discount.

    Use this tool when customers ask for a discount,
    negotiate prices, or want to know their discounted total.

    This tool does not accept a requested discount
    percentage. All discounts are calculated using
    the business's configured rules.

    Args:
        session_id: The customer's active shopping session.

    Returns:
        Discount eligibility, percentage, amount,
        and discounted cart subtotal.
    """

    if not session_id or not session_id.strip():
        return {
            "success": False,
            "message": "A valid customer session is required.",
        }

    discount_service = DiscountService(session_id.strip())
    result = discount_service.calculate_discount()

    return {
        "success": True,
        **result,
    }
