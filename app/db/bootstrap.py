import asyncio

from sqlalchemy import text
from sqlalchemy import select
from app.db.session import engine, Session
from app.db.models import Base, Category, Stone, Product
from app.data import catalog

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_base_ref_data():
    async with Session() as s:
        for code in ("bracelets", "necklaces"):
            if not (await s.execute(select(Category).where(Category.code == code))).scalar_one_or_none():
                s.add(Category(code=code))
        for code in ("amethyst", "citrine", "garnet"):
            if not (await s.execute(select(Stone).where(Stone.code == code))).scalar_one_or_none():
                s.add(Stone(code=code))
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
            item = {"id": p.id, "title": p.title, "price": p.price, "stock": p.stock}
            catalog.PRODUCTS.setdefault((category, stone), []).append(item)
            catalog.PRODUCTS_BY_ID[p.id] = item


async def init_db_and_load_cache():
    await init_db()
    await ensure_base_ref_data()
    await load_catalog_to_memory()


async def warmup_db_pool():
    sessions = [Session() for _ in range(5)]
    try:
        await asyncio.gather(*[
            s.execute(text("SELECT 1")) for s in sessions
        ])
    finally:
        await asyncio.gather(*[s.close() for s in sessions])
