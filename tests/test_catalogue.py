
from tools.catalogue import search_catalogue

print("\nSEARCH FOR RICE")
print(search_catalogue(query="rice"))

print("\nSEARCH BY CATEGORY")
print(search_catalogue(category="Soups"))

print("\nSEARCH BY PRICE")
print(search_catalogue(max_price=1500))
