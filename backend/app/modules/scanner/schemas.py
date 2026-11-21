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


class CollectionSessionPage(BaseModel):
    items: list[CollectionSessionOut]
    total: int
    limit: int
    offset: int


class CollectionSessionPatch(BaseModel):
    title: str = Field(max_length=200)

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class AnyPulseAccessOut(BaseModel):
    """Whether this account may send collections to AnyPulse (revoked only by a platform
    administrator). Nothing about AnyPulse's data."""
    state: Literal["active", "revoked"]
    revoked_at: datetime | None
    revoke_reason: str | None


# ---- screenshots (phase 3) --------------------------------------------------------------------
class UploadedFileOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    url: str             # authenticated Scanner API URL; files are never public


class ItemOut(BaseModel):
    id: uuid.UUID
    source_type: str
    status: str
    platform: str | None
    content_type: str | None
    position: int
    source_url: str | None
    possible_duplicate_of: uuid.UUID | None
    created_at: datetime
    file: UploadedFileOut | None


class FileResultOut(BaseModel):
    filename: str
    status: Literal["added", "duplicate", "rejected"]
    code: str | None
    item_id: uuid.UUID | None


class UploadOut(BaseModel):
    """Per-file outcome; one rejected file never blocks the others."""
    results: list[FileResultOut]
    items: list[ItemOut]
    session: CollectionSessionOut


class ReorderIn(BaseModel):
    item_ids: list[uuid.UUID] = Field(max_length=2000)
