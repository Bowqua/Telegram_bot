from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path

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

