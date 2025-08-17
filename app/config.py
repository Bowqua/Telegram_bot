from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    BOT_TOKEN: str

    DATABASE_URL: str | None = None
    PGUSER: str | None = None
    PGPASSWORD: str | None = None
    PGHOST: str | None = None
    PGPORT: int | None = 5432
    PGDATABASE: str | None = None

settings = Settings()

PAY_PROVIDER_TOKEN = os.environ.get("PAY_PROVIDER_TOKEN", "")
PAY_CURRENCY = os.getenv("PAY_CURRENCY", "RUB")
MANAGER_IDS = []

raw = os.getenv("MANAGER_IDS", "")
if raw:
    MANAGER_IDS = [int(x.strip()) for x in raw.split(",") if x.strip()]

