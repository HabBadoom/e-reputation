import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

Locale = Literal["fr", "ar", "en"]


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    # Either create a new organization, or join one through an invitation link.
    organization_name: str | None = Field(default=None, min_length=2, max_length=200)
    invitation_token: str | None = Field(default=None, min_length=20, max_length=200)
    locale: Locale = "fr"

    @field_validator("full_name", "organization_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def _org_or_invite(self) -> "RegisterIn":
        if not self.organization_name and not self.invitation_token:
            raise ValueError("organization_name is required unless joining with an invitation")
        return self


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
    email_verified: bool
    mfa_enabled: bool
    is_platform_admin: bool
    tenant: TenantSummary
    permissions: list[str]
    tenants: list[TenantSummary]


class TokenOut(BaseModel):
    """Returned in the body only for API clients (X-Auth-Mode: token). Browsers get cookies."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 — OAuth token type, not a secret
    expires_in: int
    tenant_id: uuid.UUID


class MfaChallengeOut(BaseModel):
    """Password was correct but a second factor is required. No session is issued yet."""

    mfa_required: Literal[True] = True
    mfa_token: str
    expires_in: int


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=200)
    code: str = Field(min_length=6, max_length=20, description="6-digit code or recovery code")


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class EmailIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=12, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class MfaStatusOut(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class MfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=20)


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class SessionOut(BaseModel):
    id: uuid.UUID
    current: bool
    tenant_id: uuid.UUID
    user_agent: str | None
    ip: str | None
    started_at: datetime
    last_active_at: datetime
