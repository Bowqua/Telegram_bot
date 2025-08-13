from sqlalchemy import select
from app.db.session import Session
from app.db.models import Category, Stone, Product

async def get_or_create_category(code: str) -> int:
    async with Session() as s:
        obj = (await s.execute(select(Category).where(Category.code == code))).scalar_one_or_none()
        if obj: return obj.id
        obj = Category(code=code); s.add(obj); await s.commit(); await s.refresh(obj); return obj.id


async def get_or_create_stone(code: str) -> int:
    async with Session() as s:
        obj = (await s.execute(select(Stone).where(Stone.code == code))).scalar_one_or_none()
        if obj: return obj.id
        obj = Stone(code=code); s.add(obj); await s.commit(); await s.refresh(obj); return obj.id


async def add_product_db(category_code: str, stone_code: str, title: str, price: int, stock: int) -> int:
    cat_id = await get_or_create_category(category_code)
    stn_id = await get_or_create_stone(stone_code)
    async with Session() as s:
        p = Product(title=title, price=price, stock=stock, category_id=cat_id, stone_id=stn_id)
        s.add(p); await s.commit(); await s.refresh(p)
        return p.id


async def delete_product_db(pid: int) -> bool:
    async with Session() as s:
        res = await s.execute(select(Product).where(Product.id == pid))
        p = res.scalar_one_or_none()
        if not p: return False
        await s.delete(p); await s.commit();
        return True


async def set_stock_db(pid: int, qty: int) -> bool:
    async with Session() as s:
        res = await s.execute(select(Product).where(Product.id == pid))
        p = res.scalar_one_or_none()
        if not p: return False
        p.stock = max(0, qty); await s.commit();
        return True
