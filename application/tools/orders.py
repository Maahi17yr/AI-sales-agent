
"""Session-bound order tools for the Naija Bites agent.

Only trusted application code should call make_order_tools(session_id).
Do not expose the raw session ID as an LLM tool parameter.

The AI may prepare a checkout for review, but it cannot confirm
an order or mark a payment as successful.
"""

from strands import tool

from data.database import repository
from application.services.checkout import CheckoutService


def make_order_tools(session_id: str):
    """Create order tools scoped to a trusted customer session."""

    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A valid application session is required.")

    def session_customer():
        """Return the verified customer ID associated with this session."""

        session = repository.get_session(session_id)

        if not session or session.get("customer_id") is None:
            return None

        return session["customer_id"]

    @tool
    def create_order(
        customer_name: str,
        phone: str,
        delivery_address: str,
        delivery_area: str,
    ) -> dict:
        """Prepare an order summary for customer review, NOT placement.

        This tool does not create an order, deduct stock, or confirm payment.

        Args:
            customer_name: Name for the delivery.
            phone: Phone number for the delivery.
            delivery_address: Complete delivery address.
            delivery_area: Supported delivery area.
        """

        print(f"[CHECKOUT DEBUG] create_order ENTERED session={session_id!r} area={delivery_area!r}", flush=True)
        customer_id = session_customer()

        if customer_id is None:
            print("[CHECKOUT DEBUG] REJECTED: unverified session", flush=True)
            return {
                "success": False,
                "message": "Customer session is not verified.",
            }

        details = (
            customer_name,
            phone,
            delivery_address,
            delivery_area,
        )

        if not all(
            isinstance(value, str) and value.strip()
            for value in details
        ):
            print("[CHECKOUT DEBUG] REJECTED: missing checkout details", flush=True)
            return {
                "success": False,
                "message": "All checkout details are required.",
            }

        # Session ownership comes from the trusted server-bound session.
        # The supplied phone is a delivery contact, not an authentication key.
        from application.services.order_service import validate_delivery_phone
        if not validate_delivery_phone(phone.strip()):
            print("[CHECKOUT DEBUG] REJECTED: invalid delivery phone", flush=True)
            return {"success": False, "message": "Enter a valid delivery phone number."}

        try:
            result = CheckoutService(session_id).prepare_checkout(
                customer_name=customer_name.strip(),
                phone=phone.strip(),
                delivery_address=delivery_address.strip(),
                delivery_area=delivery_area.strip(),
            )
            print(f"[CHECKOUT DEBUG] prepare_checkout result: success={result.get('success')!r}, message={result.get('message')!r}", flush=True)

        except (ValueError, KeyError) as error:
            print(f"[CHECKOUT DEBUG] EXCEPTION: {type(error).__name__}: {error}", flush=True)
            return {
                "success": False,
                "message": str(error),
            }

        # IMPORTANT:
        # Never overwrite a failed checkout result with success=True.
        if not result.get("success"):
            return result

        # The checkout is only prepared.
        # The customer must still explicitly approve it through
        # the trusted application interface.
        return {
            "success": True,
            "requires_confirmation": True,
            "order_created": False,
            "summary": result["summary"],
            "message": (
                "Checkout prepared successfully. "
                "Please review the order summary. "
                "The order has NOT been placed, "
                "and no payment has been confirmed."
            ),
        }

    @tool
    def get_order_status(order_id: int) -> dict:
        """Get the current customer's order status, if authorized.

        Args:
            order_id: Order number requested by the customer.
        """

        if type(order_id) is not int or order_id <= 0:
            return {
                "success": False,
                "message": "Invalid order ID.",
            }

        if session_customer() is None:
            return {
                "success": False,
                "message": "Customer session is not verified.",
            }

        order = repository.get_order_status_for_session(
            order_id,
            session_id,
        )

        if order is None:
            return {
                "success": False,
                "message": "Order not found or access denied.",
            }

        return {
            "success": True,
            "order": order,
        }

    return [create_order, get_order_status]
