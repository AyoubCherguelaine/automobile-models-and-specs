import hashlib
from urllib.parse import urldefrag

from pydantic import BaseModel, Field, HttpUrl, field_validator


def url_key(url: str) -> str:
    # Full SHA-256 replaces collision-prone PHP CRC32 hashes.
    return hashlib.sha256(urldefrag(url.strip())[0].encode()).hexdigest()


class BrandData(BaseModel):
    url: HttpUrl
    name: str = Field(min_length=1)
    logo: str | None = None
    country: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is blank")
        return value


class EngineData(BaseModel):
    other_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    specs: dict[str, dict[str, str]] = Field(default_factory=dict)
    columns: dict[str, str | int | float | None] = Field(default_factory=dict)


class AutomobileData(BaseModel):
    url: HttpUrl
    brand_name: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    press_release: str | None = None
    photos: list[str] | None = None
    engines: list[EngineData] = Field(default_factory=list)
    information: dict[str, str | int | bool | None] = Field(default_factory=dict)
