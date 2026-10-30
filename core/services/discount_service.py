
from application import config
from core.services.cart import CartService


class DiscountService:
    """Calculate discounts using business-approved rules."""

    def __init__(self, session_id):
        self.cart = CartService(session_id)

    def calculate_discount(self):
        # Always retrieve the subtotal from our cart service.
        # Never trust a subtotal supplied by the customer or AI.
        cart_summary = self.cart.view_cart()
        subtotal = cart_summary["subtotal"]

        result = {
            "eligible": False,
            "subtotal": subtotal,
            "discount_percent": 0,
            "discount_amount": 0,
            "total_after_discount": subtotal,
            "currency": config.CURRENCY,
            "message": "",
        }

        if not config.DISCOUNTS_ENABLED:
            result["message"] = "Discounts are currently unavailable."
            return result

        if not cart_summary["items"]:
            result["message"] = "Your cart is empty."
            return result

        if subtotal < config.MIN_ORDER_FOR_DISCOUNT:
            result["message"] = (
                f"Bulk discounts start from "
                f"NGN {config.MIN_ORDER_FOR_DISCOUNT:,}."
            )
            return result

        # Find the highest qualifying bulk discount tier.
        discount_percent = 0

        for minimum_amount, percentage in sorted(
            config.BULK_DISCOUNT_TIERS.items()
        ):
            if subtotal >= minimum_amount:
                discount_percent = percentage

        # Enforce the absolute discount cap.
        discount_percent = max(
            0,
            min(discount_percent, config.MAX_DISCOUNT_PERCENT)
        )

        if discount_percent == 0:
            result["message"] = "No bulk discount is available."
            return result

        # Integer naira calculation: round half up.
        discount_amount = (
            subtotal * discount_percent + 50
        ) // 100

        total = subtotal - discount_amount

        result.update({
            "eligible": True,
            "discount_percent": discount_percent,
            "discount_amount": discount_amount,
            "total_after_discount": total,
            "message": (
                f"You qualify for a {discount_percent}% "
                f"bulk-order discount!"
            ),
        })

        return result
