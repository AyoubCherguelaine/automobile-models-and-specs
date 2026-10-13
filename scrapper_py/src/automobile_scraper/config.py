from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRAPER_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/automobiles.db"
    data_dir: Path = Path("data")
    base_url: str = "https://www.autoevolution.com"
    transport: Literal["browser", "http"] = "browser"
    concurrency: int = Field(default=2, ge=1, le=16)
    request_interval: float = Field(default=1.5, ge=0)
    timeout: float = Field(default=45, gt=0)
    attempts: int = Field(default=3, ge=1, le=10)
    headless: bool = True
    respect_robots: bool = True
    user_agent: str = "AutomobileSpecsScraper/1.0"

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        return value.rstrip("/")
