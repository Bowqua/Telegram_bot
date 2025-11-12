from __future__ import annotations
from pathlib import Path
from typing import List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"

def parse_int_list(v: Union[str, List[int], None]) -> List[int]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [int(x) for x in v]

    s = str(v).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            import json
            data = json.loads(s)
            return [int(x) for x in data]
        except Exception:
            pass
    return [int(x.strip()) for x in s.split(",") if x.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BOT_TOKEN: str

    DATABASE_URL: str | None = None
    PGUSER: str | None = None
    PGPASSWORD: str | None = None
    PGHOST: str | None = None
    PGPORT: int | None = 5432
    PGDATABASE: str | None = None

    ADMIN_IDS: List[int] = Field(default_factory=list)
    MANAGER_IDS: List[int] = Field(default_factory=list)

    PAY_PROVIDER_TOKEN: str = ""
    PAY_CURRENCY: str = "RUB"

    @field_validator("ADMIN_IDS", "MANAGER_IDS", mode="before")
    @classmethod
    def _parse_ids(cls, v):
        return parse_int_list(v)

settings = Settings()
