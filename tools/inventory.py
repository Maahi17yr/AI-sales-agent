
from strands import tool
from database import repository


@tool
def check_stock(
    product_id: int,
    quantity: int = 1,
    variant: str = "standard",
) -> dict:
    """
    Check whether a product has enough stock.

    Use this tool when customers ask if a food item
    is available or request a specific quantity.

    Args:
        product_id: The product's catalogue ID.
        quantity: Number of portions requested.
        variant: Product variant, default is standard.

    Returns:
        Stock availability and quantity information.
    """

    if quantity <= 0:
        return {
            "success": False,
            "message": "Quantity must be greater than zero.",
        }

    product = repository.get_product(product_id)

    if not product:
        return {
            "success": False,
            "message": "Product not found.",
        }

    stock = repository.get_stock(product_id, variant)

    available = bool(product["available"]) and stock >= quantity

    return {
        "success": True,
        "product_id": product_id,
        "product_name": product["name"],
        "variant": variant,
        "requested_quantity": quantity,
        "stock_quantity": stock,
        "available": available,
        "message": (
            f"{quantity} portion(s) of {product['name']} available."
            if available
            else f"Only {stock} portion(s) of {product['name']} available."
        ),
    }
