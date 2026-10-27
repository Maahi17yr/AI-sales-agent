
from tools.delivery import get_delivery_fee

print("\nTEST 1: Ikeja")
print(get_delivery_fee("Ikeja"))

print("\nTEST 2: Lekki Phase 1")
print(get_delivery_fee("Lekki Phase 1"))

print("\nTEST 3: Unsupported area")
print(get_delivery_fee("Ibadan"))

print("\nTEST 4: Empty area")
print(get_delivery_fee(""))
