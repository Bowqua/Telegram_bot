# app/db/session.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from urllib.parse import quote_plus
from uuid import uuid4
import ssl
from app.config import settings

user = settings.PGUSER.strip()
pwd  = quote_plus(settings.PGPASSWORD)
host = settings.PGHOST.strip()
port = int(settings.PGPORT or 6543)
db   = settings.PGDATABASE.strip()

DB_URL = f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{db}?prepared_statement_cache_size=0"

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

engine = create_async_engine(
    DB_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={
        "ssl": ssl_ctx,
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__sa_asyncpg_{uuid4()}__",
    },
)
Session = async_sessionmaker(engine, expire_on_commit=False)
