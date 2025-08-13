PRODUCTS = {
    ("bracelets", "amethyst"): [
        {"id": 101, "title": "Браслет «Аметист люкс»", "price": 3500, "stock": 5},
        {"id": 102, "title": "Браслет «Фиолетовый иней»", "price": 2900, "stock": 3},
        {"id": 103, "title": "Браслет «Лавандовый свет»", "price": 3100, "stock": 2},
    ],
    ("bracelets", "citrine"): [
        {"id": 104, "title": "Браслет «Золотой цитрин»", "price": 3300, "stock": 4},
        {"id": 105, "title": "Браслет «Солнечная капля»", "price": 2800, "stock": 6},
    ],
    ("necklaces", "garnet"): [
        {"id": 201, "title": "Ожерелье «Гранатовая ночь»", "price": 5200, "stock": 2},
        {"id": 202, "title": "Ожерелье «Рубиновый шёлк»", "price": 5700, "stock": 1},
    ],
}

PRODUCTS_BY_ID = {}
for key, items in PRODUCTS.items():
    for p in items:
        PRODUCTS_BY_ID[p["id"]] = p