"""Brands and brand aliases. First tenant-scoped business resource."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.core.errors import Conflict, NotFound
from app.db.models import Brand, BrandAlias
from app.db.session import get_db
from app.modules.audit.service import record
from app.modules.brands.scripts import guess_script

router = APIRouter(prefix="/brands", tags=["brands"])

Script = Literal["latin", "arabic", "arabizi", "tifinagh", "other"]
AliasKind = Literal["alias", "misspelling", "transliteration", "product", "abbreviation"]
BrandStatus = Literal["active", "paused", "archived"]

class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    script: Script | None = None
    kind: AliasKind = "alias"

    @field_validator("alias")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be blank")
        return v


class AliasOut(BaseModel):
    id: uuid.UUID
    alias: str
    script: str
    kind: str
    created_at: datetime


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    website: HttpUrl | None = None
    country: str = Field(default="DZ", pattern=r"^[A-Z]{2}$")
    primary_language: str = Field(default="fr", pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
    aliases: list[AliasIn] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be blank")
        return v


class BrandPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    website: HttpUrl | None = None
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    primary_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
    status: BrandStatus | None = None


class BrandOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    website: str | None
    country: str
    primary_language: str
    status: str
    created_at: datetime
    updated_at: datetime
    aliases: list[AliasOut]


class BrandPage(BaseModel):
    items: list[BrandOut]
    total: int
    limit: int
    offset: int


def _out(b: Brand, aliases: list[BrandAlias]) -> BrandOut:
    return BrandOut(
        id=b.id, name=b.name, description=b.description, website=b.website, country=b.country,
        primary_language=b.primary_language, status=b.status, created_at=b.created_at,
        updated_at=b.updated_at,
        aliases=[AliasOut(id=a.id, alias=a.alias, script=a.script, kind=a.kind,
                          created_at=a.created_at) for a in aliases],
    )


def _aliases(db: Session, brand_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[BrandAlias]]:
    out: dict[uuid.UUID, list[BrandAlias]] = {i: [] for i in brand_ids}
    if brand_ids:
        for a in db.scalars(select(BrandAlias).where(BrandAlias.brand_id.in_(brand_ids))
                            .order_by(BrandAlias.created_at)):
            out[a.brand_id].append(a)
    return out


def _get(db: Session, ctx: AuthContext, brand_id: uuid.UUID) -> Brand:
    # RLS already restricts to the tenant; the explicit filter is defence in depth and makes
    # the intent obvious to readers.
    b = db.scalars(select(Brand).where(Brand.id == brand_id,
                                       Brand.tenant_id == ctx.tenant_id)).one_or_none()
    if b is None:
        raise NotFound("Brand not found")  # same response whether missing or another tenant's
    return b


def _flush_unique(db: Session, what: str) -> None:
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict(f"{what} already exists", code="duplicate") from e


@router.get("", response_model=BrandPage)
def list_brands(
    ctx: AuthContext = Depends(require("brands:read")),
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, max_length=200),
    status: BrandStatus | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> BrandPage:
    stmt = select(Brand).where(Brand.tenant_id == ctx.tenant_id)
    if status:
        stmt = stmt.where(Brand.status == status)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        alias_match = select(BrandAlias.brand_id).where(BrandAlias.alias.ilike(like))
        stmt = stmt.where(or_(Brand.name.ilike(like), Brand.id.in_(alias_match)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    brands = list(db.scalars(stmt.order_by(Brand.name).limit(limit).offset(offset)))
    al = _aliases(db, [b.id for b in brands])
    return BrandPage(items=[_out(b, al[b.id]) for b in brands], total=total, limit=limit,
                     offset=offset)


@router.post("", response_model=BrandOut, status_code=201)
def create_brand(body: BrandIn, request: Request,
                 ctx: AuthContext = Depends(require("brands:write")),
                 db: Session = Depends(get_db)) -> BrandOut:
    b = Brand(tenant_id=ctx.tenant_id, name=body.name, description=body.description,
              website=str(body.website) if body.website else None, country=body.country,
              primary_language=body.primary_language, created_by=ctx.user_id)
    db.add(b)
    _flush_unique(db, "A brand with this name")
    seen: set[str] = set()
    aliases = []
    for a in body.aliases:
        if a.alias.lower() in seen:
            continue
        seen.add(a.alias.lower())
        alias = BrandAlias(tenant_id=ctx.tenant_id, brand_id=b.id, alias=a.alias,
                           script=a.script or guess_script(a.alias), kind=a.kind)
        db.add(alias)
        aliases.append(alias)
    db.flush()
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="brand.created",
           target_type="brand", target_id=b.id, meta={"name": b.name}, request=request)
    db.commit()
    return _out(b, aliases)


@router.get("/{brand_id}", response_model=BrandOut)
def get_brand(brand_id: uuid.UUID, ctx: AuthContext = Depends(require("brands:read")),
              db: Session = Depends(get_db)) -> BrandOut:
    b = _get(db, ctx, brand_id)
    return _out(b, _aliases(db, [b.id])[b.id])


@router.patch("/{brand_id}", response_model=BrandOut)
def update_brand(brand_id: uuid.UUID, body: BrandPatch, request: Request,
                 ctx: AuthContext = Depends(require("brands:write")),
                 db: Session = Depends(get_db)) -> BrandOut:
    b = _get(db, ctx, brand_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if k == "website":
            v = str(v) if v else None
        if k == "name" and v:
            v = " ".join(v.split())
        setattr(b, k, v)
    b.updated_at = datetime.now(UTC)
    _flush_unique(db, "A brand with this name")
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="brand.updated",
           target_type="brand", target_id=b.id, meta={"fields": sorted(changes)}, request=request)
    db.commit()
    return _out(b, _aliases(db, [b.id])[b.id])


@router.delete("/{brand_id}", status_code=204)
def delete_brand(brand_id: uuid.UUID, request: Request,
                 ctx: AuthContext = Depends(require("brands:delete")),
                 db: Session = Depends(get_db)) -> Response:
    b = _get(db, ctx, brand_id)
    db.delete(b)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="brand.deleted",
           target_type="brand", target_id=brand_id, meta={"name": b.name}, request=request)
    db.commit()
    return Response(status_code=204)


@router.post("/{brand_id}/aliases", response_model=AliasOut, status_code=201)
def add_alias(brand_id: uuid.UUID, body: AliasIn, request: Request,
              ctx: AuthContext = Depends(require("brands:write")),
              db: Session = Depends(get_db)) -> AliasOut:
    b = _get(db, ctx, brand_id)
    a = BrandAlias(tenant_id=ctx.tenant_id, brand_id=b.id, alias=body.alias,
                   script=body.script or guess_script(body.alias), kind=body.kind)
    db.add(a)
    _flush_unique(db, "This alias")
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="brand.alias_added",
           target_type="brand", target_id=b.id, meta={"alias": a.alias}, request=request)
    db.commit()
    return AliasOut(id=a.id, alias=a.alias, script=a.script, kind=a.kind, created_at=a.created_at)


@router.delete("/{brand_id}/aliases/{alias_id}", status_code=204)
def delete_alias(brand_id: uuid.UUID, alias_id: uuid.UUID, request: Request,
                 ctx: AuthContext = Depends(require("brands:write")),
                 db: Session = Depends(get_db)) -> Response:
    b = _get(db, ctx, brand_id)
    a = db.scalars(select(BrandAlias).where(BrandAlias.id == alias_id,
                                            BrandAlias.brand_id == b.id)).one_or_none()
    if a is None:
        raise NotFound("Alias not found")
    db.delete(a)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="brand.alias_removed",
           target_type="brand", target_id=b.id, meta={"alias": a.alias}, request=request)
    db.commit()
    return Response(status_code=204)
