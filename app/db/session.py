from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from urllib.parse import quote_plus
from app.config import settings

if settings.DATABASE_URL:
    DB_URL = settings.DATABASE_URL

elif settings.PGUSER and settings.PGPASSWORD and settings.PGHOST and settings.PGDATABASE:
    pwd = quote_plus(settings.PGPASSWORD)
    DB_URL = f"postgresql+asyncpg://{settings.PGUSER}:{pwd}@{settings.PGHOST}:{settings.PGPORT}/{settings.PGDATABASE}"

else:
    DB_URL = "sqlite+aiosqlite:///./app.db"

print("[DB] Using:", DB_URL.split('://')[0], "→", ("cloud" if "postgresql+asyncpg" in DB_URL else "sqlite"))

engine = create_async_engine(
    DB_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0, "timeout": 60},
)
Session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
