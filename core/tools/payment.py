
import secrets
import sqlite3

from strands import tool
from data.database.models import get_connection


@tool
def create_payment_link(order_id: int) -> dict:
    """
    Generate a simulated payment link for an existing order.

    Use this tool when a customer requests payment
    instructions for an order awaiting payment.

    This tool does not process money or mark orders paid.

    Args:
        order_id: ID of the existing customer order.

    Returns:
        A mock payment reference, link, and payment status.
    """

    if type(order_id) is not int or order_id <= 0:
        return {
            "success": False,
            "message": "A valid order ID is required.",
        }

    conn = get_connection()

    try:
        # Keep order checks and payment creation together.
        conn.execute("BEGIN IMMEDIATE")

        order = conn.execute(
            """
            SELECT id, total, status
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            raise ValueError("Order not found.")

        # Return an existing payment record instead
        # of creating another one.
        existing = conn.execute(
            """
            SELECT payment_reference, payment_link,
                   amount, status
            FROM payments
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()

        if existing:
            conn.commit()

            return {
                "success": True,
                "order_id": order_id,
                "payment_reference": existing["payment_reference"],
                "payment_link": existing["payment_link"],
                "amount": existing["amount"],
                "currency": "NGN",
                "payment_status": existing["status"],
                "message": "Existing mock payment record retrieved.",
            }

        if order["status"] != "pending_payment":
            raise ValueError(
                "This order is not awaiting payment."
            )

        reference = "MOCK-" + secrets.token_hex(8).upper()

        # Deliberately not a real website.
        payment_link = f"mock://pay/{reference}"

        conn.execute(
            """
            INSERT INTO payments (
                order_id,
                amount,
                payment_reference,
                payment_link,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                order_id,
                order["total"],
                reference,
                payment_link,
                "unpaid",
            ),
        )

        conn.commit()

        return {
            "success": True,
            "order_id": order_id,
            "payment_reference": reference,
            "payment_link": payment_link,
            "amount": order["total"],
            "currency": "NGN",
            "payment_status": "unpaid",
            "message": (
                "Mock payment link generated. "
                "This is a simulation and cannot accept real payments."
            ),
        }

    except (ValueError, sqlite3.Error) as error:
        conn.rollback()

        return {
            "success": False,
            "message": str(error),
        }

    finally:
        conn.close()
