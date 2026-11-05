import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Locale = Literal["fr", "ar", "en"]


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)
    locale: Locale = "fr"

    @field_validator("full_name", "organization_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    tenant_id: uuid.UUID | None = None


class RefreshIn(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=200)


class SwitchTenantIn(BaseModel):
    tenant_id: uuid.UUID


class AcceptInvitationIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class TenantSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    locale: str
    tenant: TenantSummary
    permissions: list[str]
    tenants: list[TenantSummary]


class TokenOut(BaseModel):
    """Returned in the body only for API clients (X-Auth-Mode: token). Browsers get cookies."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    tenant_id: uuid.UUID
