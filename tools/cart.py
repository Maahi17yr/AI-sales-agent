"""Session-scoped Strands tools for the Naija Bites shopping cart.

Save this as tools/cart.py. The LLM never chooses a session ID and cannot
approve checkout or change inventory through these tools.
"""
from strands import tool

from database import repository
from services.cart import CartService
from services.customization_service import CustomizationService


def make_cart_tools(session_id: str):
    """Called by trusted application code with its own session identifier."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A valid session ID is required.")

    def authorized_cart():
        session = repository.get_session(session_id)
        if not session or session.get("customer_id") is None:
            return None
        return CartService(session_id)

    @tool
    def get_customization_options(product_id: int) -> dict:
        """Show removable ingredients and permitted extras for a meal.

        Args:
            product_id: Catalogue product ID of the meal.
        """
        if authorized_cart() is None:
            return {"success": False, "message": "Customer session is not verified."}
        return CustomizationService.get_options(product_id)

    @tool
    def add_to_cart(
        product_id: int,
        quantity: int = 1,
        remove: list[str] | None = None,
        add: dict[str, int] | None = None,
    ) -> dict:
        """Add a standard or customized meal to the current customer's cart.

        Args:
            product_id: Product ID from the catalogue.
            quantity: Number of identical plates to add.
            remove: Included ingredient names to remove, e.g. ['Boiled Egg'].
            add: Extra ingredient names and quantities per plate, e.g. {'Fried Plantain': 1}.
        """
        cart = authorized_cart()
        if cart is None:
            return {"success": False, "message": "Customer session is not verified."}
        return cart.add_item(product_id=product_id, quantity=quantity, remove=remove, add=add)

    @tool
    def view_cart() -> dict:
        """Show the current customer's cart, customizations and calculated subtotal."""
        cart = authorized_cart()
        if cart is None:
            return {"success": False, "message": "Customer session is not verified."}
        return {"success": True, **cart.view_cart()}

    @tool
    def set_cart_line_quantity(line_number: int, quantity: int) -> dict:
        """Change the quantity of an exact cart line; zero removes the line.

        Use the one-based line number from view_cart. Differently customized
        plates have separate line numbers.

        Args:
            line_number: Cart line number, starting from 1.
            quantity: New number of plates; 0 removes this line.
        """
        cart = authorized_cart()
        if cart is None:
            return {"success": False, "message": "Customer session is not verified."}
        if type(line_number) is not int or line_number <= 0:
            return {"success": False, "message": "Invalid line number."}
        lines = cart.get_cart()
        if line_number > len(lines):
            return {"success": False, "message": "Cart line not found. View the cart again."}
        line = lines[line_number - 1]
        return cart.update_quantity(
            product_id=line["product_id"],
            quantity=quantity,
            variant=line.get("variant", "standard"),
            modifications=line.get("modifications", []),
        )

    @tool
    def clear_cart() -> dict:
        """Remove all items from the current customer's cart."""
        cart = authorized_cart()
        if cart is None:
            return {"success": False, "message": "Customer session is not verified."}
        return cart.clear_cart()

    return [get_customization_options, add_to_cart, view_cart,
            set_cart_line_quantity, clear_cart]
