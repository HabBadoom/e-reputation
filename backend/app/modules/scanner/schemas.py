import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Locale = Literal["fr", "ar", "en"]


class ScannerRegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    locale: Locale = "fr"

    @field_validator("full_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class ScannerLoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ScannerRefreshIn(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=200)


class ScannerTokenOut(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 — OAuth token type, not a secret
    expires_in: int


class ScannerMeOut(BaseModel):
    account_id: uuid.UUID
    email: str
    full_name: str
    locale: Locale
    created_at: datetime


class CountersOut(BaseModel):
    """Collection metrics only. Scanner never shows analytical figures."""
    items: int
    screenshots: int
    urls: int
    imports: int
    needs_review: int
    ready: int
    failed: int


class DestinationOut(BaseModel):
    """Where the collection will be sent: an AnyPulse organization (through a link) and one
    of its brands. Names are snapshots; Scanner cannot read AnyPulse data."""
    link_id: uuid.UUID
    organization_name: str
    link_active: bool
    brand_id: uuid.UUID | None
    brand_name: str | None


class CollectionSessionOut(BaseModel):
    id: uuid.UUID
    status: str
    title: str
    created_at: datetime
    updated_at: datetime
    transmitted_at: datetime | None
    anypulse_collection_id: uuid.UUID | None
    is_open: bool
    counters: CountersOut
    destination: DestinationOut | None


class CollectionSessionPage(BaseModel):
    items: list[CollectionSessionOut]
    total: int
    limit: int
    offset: int


class CollectionSessionPatch(BaseModel):
    """Fields left out are unchanged. `link_id: null` clears the destination."""
    title: str | None = Field(default=None, max_length=200)
    link_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else v


class LinkStartOut(BaseModel):
    authorize_url: str
    expires_at: datetime


class LinkOut(BaseModel):
    id: uuid.UUID
    organization_name: str
    status: str
    created_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None


class BrandOut(BaseModel):
    id: uuid.UUID
    name: str
