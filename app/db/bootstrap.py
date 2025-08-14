from sqlalchemy import select, text
from app.db.session import engine, Session
from app.db.models import Base, Category, Stone, Product
from app.data import catalog

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        dialect = conn.engine.dialect.name
        if dialect == "postgresql":
            await conn.exec_driver_sql("ALTER TABLE categories ADD COLUMN IF NOT EXISTS name_ru VARCHAR(128);")
            await conn.exec_driver_sql("ALTER TABLE stones ADD COLUMN IF NOT EXISTS name_ru VARCHAR(128);")
            await conn.exec_driver_sql("ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT;")
            await conn.exec_driver_sql("ALTER TABLE products ADD COLUMN IF NOT EXISTS photos JSONB NOT NULL DEFAULT '[]'::jsonb;")
            await conn.exec_driver_sql("UPDATE categories SET name_ru = COALESCE(name_ru, code);")
            await conn.exec_driver_sql("UPDATE stones     SET name_ru = COALESCE(name_ru, code);")
        else:
            cols = {r[1] for r in (await conn.exec_driver_sql("PRAGMA table_info(categories)")).fetchall()}
            if "name_ru" not in cols:
                await conn.exec_driver_sql("ALTER TABLE categories ADD COLUMN name_ru VARCHAR(128);")
                await conn.exec_driver_sql("UPDATE categories SET name_ru = code;")
            cols = {r[1] for r in (await conn.exec_driver_sql("PRAGMA table_info(stones)")).fetchall()}
            if "name_ru" not in cols:
                await conn.exec_driver_sql("ALTER TABLE stones ADD COLUMN name_ru VARCHAR(128);")
                await conn.exec_driver_sql("UPDATE stones SET name_ru = code;")
            cols = {r[1] for r in (await conn.exec_driver_sql("PRAGMA table_info(products)")).fetchall()}
            if "description" not in cols:
                await conn.exec_driver_sql("ALTER TABLE products ADD COLUMN description TEXT;")
            if "photos" not in cols:
                await conn.exec_driver_sql("ALTER TABLE products ADD COLUMN photos TEXT DEFAULT '[]' NOT NULL;")


async def ensure_base_ref_data():
    async with Session() as s:
        for code, name_ru in [("bracelets", "Браслеты"), ("necklaces", "Ожерелья")]:
            if not (await s.execute(select(Category).where(Category.code == code))).scalar_one_or_none():
                s.add(Category(code=code, name_ru=name_ru))
        for code, name_ru in [("amethyst", "Аметист"), ("citrine", "Цитрин"), ("garnet", "Гранат")]:
            if not (await s.execute(select(Stone).where(Stone.code == code))).scalar_one_or_none():
                s.add(Stone(code=code, name_ru=name_ru))
        await s.commit()


async def load_catalog_to_memory():
    catalog.PRODUCTS.clear()
    catalog.PRODUCTS_BY_ID.clear()
    catalog.CAT_LABELS.clear()
    catalog.STONE_LABELS.clear()

    async with Session() as session:
        cats = (await session.execute(select(Category))).scalars().all()
        stns = (await session.execute(select(Stone))).scalars().all()
        prods = (await session.execute(select(Product))).scalars().all()

        id2cat = {c.id: c.code for c in cats}
        id2stn = {s.id: s.code for s in stns}
        for c in cats:
            catalog.CAT_LABELS[c.code] = c.name_ru or c.code
        for s in stns:
            catalog.STONE_LABELS[s.code] = s.name_ru or s.code

        for p in prods:
            cat_code = id2cat.get(p.category_id)
            stn_code = id2stn.get(p.stone_id)
            if not cat_code or not stn_code:
                continue
            item = {
                "id": p.id,
                "title": p.title,
                "price": p.price,
                "stock": p.stock,
                "description": p.description,
                "photos": (p.photos or []),
            }
            catalog.PRODUCTS.setdefault((cat_code, stn_code), []).append(item)
            catalog.PRODUCTS_BY_ID[p.id] = item


async def init_db_and_load_cache():
    await init_db()
    await ensure_base_ref_data()
    await load_catalog_to_memory()


def cache_delete_product(product_id: int) -> None:
    item = catalog.PRODUCTS_BY_ID.pop(product_id, None)
    if not item:
        return

    for key, items in list(catalog.PRODUCTS.items()):
        catalog.PRODUCTS[key] = [x for x in items if x["id"] != product_id]
        if not catalog.PRODUCTS[key]:
            del catalog.PRODUCTS[key]


def cache_upsert_product(category: str, stone: str, item: dict) -> None:
    catalog.PRODUCTS_BY_ID[item["id"]] = item
    lst = catalog.PRODUCTS.setdefault((category, stone), [])
    for i, it in enumerate(lst):
        if it["id"] == item["id"]:
            lst[i] = item
            break
    else:
        lst.append(item)


async def cache_refresh_single(session, product_id: int) -> None:
    from sqlalchemy import select
    from app.db.models import Product, Category, Stone
    row = (await session.execute(
        select(Product, Category.code, Stone.code)
        .join(Category, Category.id == Product.category_id)
        .join(Stone, Stone.id == Product.stone_id)
        .where(Product.id == product_id)
    )).first()
    if not row:
        cache_delete_product(product_id)
        return
    p, cat_code, st_code = row
    item = {
        "id": p.id,
        "title": p.title,
        "price": p.price,
        "stock": p.stock,
        "description": p.description,
        "photos": p.photos or [],
    }
    cache_upsert_product(cat_code, st_code, item)
