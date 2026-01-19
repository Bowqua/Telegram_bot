import os, importlib, asyncio, pytest
from pathlib import Path
from sqlalchemy import select
from unicodedata import category


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def temporary_database_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.db"
    return path


@pytest.fixture(scope="session", autouse=True)
def env_for_tests(temporary_database_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "000:TEST")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{temporary_database_path}")
    monkeypatch.setenv("ADMIN_IDS", "111111111, 222222222")
    monkeypatch.setenv("MANAGER_IDS", "111,222")


@pytest.fixture(scope="session")
def app_session_modules():
    import app.db.session as session_mod
    import app.db.bootstrap as bootstrap_mod
    import app.db.models as model_mod
    import app.data.catalog as catalog_mod

    session_mod = importlib.reload(session_mod)
    bootstrap_mod = importlib.reload(bootstrap_mod)
    model_mod = importlib.reload(model_mod)
    catalog_mod = importlib.reload(catalog_mod)

    return session_mod, bootstrap_mod, model_mod, catalog_mod


@pytest.fixture(scope="session")
def engine(app_session_modules):
    session_mod, _, _, _ = app_session_modules
    return session_mod.create_engine


@pytest.fixture(scope="session")
def session(app_session_modules):
    session_mod, _, _, _ = app_session_modules
    return session_mod.Session


@pytest.fixture(scope="session", autouse=True)
async def create_shema(app_session_modules):
    _, bootstrap_mod, _, _ = app_session_modules
    await bootstrap_mod.init_db()


@pytest.fixture
async def db_session(Session):
    async with Session() as s:
        yield s


@pytest.fixture
async def seed_data(app_session_modules, db_session):
    _, _, models, _ = app_session_modules
    Category, Stone, Product = models.Category, models.Stone, models.Product
    category = Category(code="bracelets", name_ru="Браслеты")
    stone = Stone(code="amethyst", name_ru="Аметист")

    await db_session.merge(category)
    await db_session.merge(stone)
    await db_session.commit()

    c = (await db_session.execute(select(Category).where(Category.code == "bracelets"))).scalar_one()
    s = (await db_session.execute(select(Stone).where(Stone.code == "amethyst"))).scalar_one()
    prod = Product(
        title="Браслет с аметистом",
        price=3000,
        stock=5,
        description="A stylish bracelet",
        photos=["a.jpg", "b.jpg"],
        category_id=c.id,
        stone_id=s.id,
    )
    db_session.add(prod)
    await db_session.commit()

    return {"category": c, "stone": s, "product": prod}