PRODUCTS = {}
PRODUCTS_BY_ID = {}

CAT_LABELS = {}
STONE_LABELS = {}

for key, items in PRODUCTS.items():
    for p in items:
        PRODUCTS_BY_ID[p["id"]] = p