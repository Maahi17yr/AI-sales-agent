"""Pure catalogue-name validation with no agent or database dependencies."""


def match_exact_orderable_product(product_name: str, products: list[dict]) -> dict:
    """Resolve exactly one available catalogue product without substitutions."""
    if not isinstance(product_name, str) or not product_name.strip():
        return {"success": False, "message": "A product name is required."}

    name = " ".join(product_name.split())
    exact = [
        product for product in products
        if isinstance(product.get("name"), str)
        and " ".join(product["name"].split()).casefold() == name.casefold()
    ]
    if not exact:
        return {
            "success": False,
            "message": f"{name} is not available under that exact name. Search the menu and ask before substituting.",
        }
    if len(exact) != 1:
        return {
            "success": False,
            "message": "Multiple catalogue products have that name. Please ask for clarification.",
        }
    return {"success": True, "product": exact[0]}
