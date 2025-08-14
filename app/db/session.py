from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from urllib.parse import quote_plus
from app.config import settings

user = (settings.PGUSER or "").strip()
pwd  = quote_plus(settings.PGPASSWORD or "")
host = (settings.PGHOST or "").strip()
port = settings.PGPORT or 5432
db   = (settings.PGDATABASE or "").strip()

if settings.DATABASE_URL:
    DB_URL = settings.DATABASE_URL
elif user and pwd and host and db:
    DB_URL = f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"
else:
    DB_URL = "sqlite+aiosqlite:///./app.db"

CONNECT_ARGS = {
    "prepare_threshold": 0,   #
    "sslmode": "require",
}

engine = create_async_engine(DB_URL, pool_pre_ping=True, future=True,
                             connect_args=CONNECT_ARGS)
Session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
