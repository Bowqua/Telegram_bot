from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

DB_URL = settings.DATABASE_URL or "sqlite+aiosqlite:///./app.db"

engine = create_async_engine(
    DB_URL,
    pool_pre_ping=True,
    future=True,
    connect_args={}
)

Session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)