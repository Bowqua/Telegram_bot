from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.engine import URL
from app.config import settings
import ssl, os

def make_ssl_context() -> ssl.SSLContext:
    if os.getenv("PG_INSECURE_SSL") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

user = (settings.PGUSER or "").strip()
pwd  = (settings.PGPASSWORD or "")
host = (settings.PGHOST or "").strip()
port = settings.PGPORT or 5432
db   = (settings.PGDATABASE or "").strip()

is_cloud  = all([user, pwd, host, db])
is_pooler = ("pooler.supabase.com" in host) or ("pgbouncer" in host)

if is_cloud:
    query = {"prepared_statement_cache_size": "0"} if is_pooler else {}

    url = URL.create(
        "postgresql+asyncpg",
        username=user,
        password=pwd,
        host=host,
        port=port,
        database=db,
        query=query,
    )

    connect_args = {"ssl": make_ssl_context()}
    if is_pooler:
        connect_args["statement_cache_size"] = 0

    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,  #
        pool_timeout=10,
        connect_args=connect_args,
    )
else:
    engine = create_async_engine("sqlite+aiosqlite:///./app.db", pool_pre_ping=True)

Session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

try:
    used = "postgresql+asyncpg → cloud" if is_cloud else "sqlite+aiosqlite → sqlite"
    mode = "pooler" if is_pooler else "direct"
    print(f"[DB] Using: {used} ({mode if is_cloud else ''})".strip())
except Exception:
    pass
