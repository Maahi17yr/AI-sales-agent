from strands import tool
from database import repository


@tool
def search_catalogue(
    query: str = "",
    category: str = "",
    min_price: int = 0,
    max_price: int = 0,
) -> dict:
    """
    Search the food catalogue for available products.

    Use this tool when customers ask about food,
    prices, menu options, or product descriptions.

    Args:
        query: Product name or search keyword.
        category: Optional food category.
        min_price: Optional minimum price in NGN.
        max_price: Optional maximum price in NGN.

    Returns:
        Matching products with their prices and descriptions.
    """

    if min_price < 0 or max_price < 0:
        return {
            "success": False,
            "message": "Price filters cannot be negative.",
        }

    if max_price and min_price > max_price:
        return {
            "success": False,
            "message": "Minimum price exceeds maximum price.",
        }

    products = repository.search_products(
        query=query or None,
        category=category or None,
        min_price=min_price if min_price > 0 else None,
        max_price=max_price if max_price > 0 else None,
    )

    return {
        "success": True,
        "count": len(products),
        "products": [
            {
                "id": product["id"],
                "name": product["name"],
                "category": product["category"],
                "description": product["description"],
                "price": product["price"],
                "currency": "NGN",
                "available": bool(product["available"]),
            }
            for product in products
        ],
    }
