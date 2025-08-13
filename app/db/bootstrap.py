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
    async with Session() as session:
        cats = {c.id: c.code for c in (await session.execute(select(Category))).scalars().all()}
        stns = {st.id: st.code for st in (await session.execute(select(Stone))).scalars().all()}
        products = (await session.execute(select(Product))).scalars().all()
        for p in products:
            category = cats.get(p.category_id)
            stone = stns.get(p.stone_id)
            if not category or not stone:
                continue
            item = {"id": p.id, "title": p.title, "price": p.price, "stock": p.stock,
                    "description": p.description, "photos": p.photos or []}
            catalog.PRODUCTS.setdefault((category, stone), []).append(item)
            catalog.PRODUCTS_BY_ID[p.id] = item


async def init_db_and_load_cache():
    await init_db()
    await ensure_base_ref_data()
    await load_catalog_to_memory()
