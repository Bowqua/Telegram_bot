from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.engine import URL
from app.config import settings
import ssl, certifi

user = (settings.PGUSER or "").strip()
pwd  = settings.PGPASSWORD or ""
host = (settings.PGHOST or "").strip()
port = settings.PGPORT or 5432
db   = (settings.PGDATABASE or "").strip()

is_cloud = all([user, pwd, host, db])
is_pooler = "pooler.supabase.com" in host or "pgbouncer" in host

if is_cloud:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    query = {}
    if is_pooler:
        query["prepared_statement_cache_size"] = "0"

    url = URL.create(
        "postgresql+asyncpg",
        username=user,
        password=pwd,
        host=host,
        port=port,
        database=db,
        query=query
    )

    connect_args = {"ssl": ssl_ctx}
    if is_pooler:
        connect_args["statement_cache_size"] = 0

    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

else:
    from sqlalchemy.ext.asyncio import create_async_engine as _create
    engine = _create("sqlite+aiosqlite:///./app.db", pool_pre_ping=True)

Session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
