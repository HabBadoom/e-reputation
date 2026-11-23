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


class ExtractionOut(BaseModel):
    """Phase 4: how the screenshot was read (no analysis)."""
    status: Literal["queued", "running", "done", "failed"]
    confidence: float | None
    issues: list[str]
    context: dict[str, str]
    entries: int
    model: str | None


class ItemOut(BaseModel):
    id: uuid.UUID
    source_type: str
    status: str
    error_code: str | None = None
    platform: str | None
    content_type: str | None
    position: int
    source_url: str | None
    possible_duplicate_of: uuid.UUID | None
    created_at: datetime
    file: UploadedFileOut | None
    extraction: ExtractionOut | None = None


class EntryOut(BaseModel):
    """A post, comment or reply read from a screenshot, as the agent can review it."""
    id: uuid.UUID
    item_id: uuid.UUID
    position: int
    kind: Literal["post", "comment", "reply"]
    parent_id: uuid.UUID | None
    author_name: str
    author_is_page: bool
    text: str
    image_text: str
    script: str
    text_truncated: bool
    partially_visible: bool
    relative_time: str
    published_at: datetime | None
    published_precision: str | None
    reactions_count: int | None
    comments_count: int | None
    shares_count: int | None
    duplicate_of: uuid.UUID | None
    edited_by_agent: bool


class EntryPatch(BaseModel):
    author_name: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=20000)


class ExtractionInfoOut(BaseModel):
    enabled: bool


class QueuedOut(BaseModel):
    queued: int


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


class BrowserInfoOut(BaseModel):
    """Built-in browser availability. `ws_url` empty = same origin as the Scanner app."""
    enabled: bool
    ws_url: str
    has_saved_session: bool
    saved_at: datetime | None
