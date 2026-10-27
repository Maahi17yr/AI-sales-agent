
from services.cart import CartService

cart = CartService("test_customer_001")

print("\n1. ADDING JOLLOF")
print(cart.add_item(product_id=1, quantity=2))

print("\n2. ADDING CHICKEN")
print(cart.add_item(product_id=20, quantity=1))

print("\n3. VIEWING CART")
print(cart.view_cart())

print("\n4. UPDATING JOLLOF QUANTITY")
print(cart.update_quantity(product_id=1, quantity=3))

print("\n5. REMOVING CHICKEN")
print(cart.remove_item(product_id=20))

print("\n6. FINAL CART")
print(cart.view_cart())
