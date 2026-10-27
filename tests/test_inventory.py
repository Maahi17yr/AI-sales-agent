
from tools.inventory import check_stock

print("\nTEST 1: Available stock")
print(check_stock(product_id=1, quantity=3))

print("\nTEST 2: Excessive quantity")
print(check_stock(product_id=1, quantity=100))

print("\nTEST 3: Invalid product")
print(check_stock(product_id=999, quantity=2))

print("\nTEST 4: Invalid quantity")
print(check_stock(product_id=1, quantity=-2))
