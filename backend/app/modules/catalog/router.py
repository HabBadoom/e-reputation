"""Per-brand catalog: products, competitors and monitoring keywords."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import AfterValidator, BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.core.errors import Conflict, NotFound
from app.db.models import Brand, BrandKeyword, Competitor, Product
from app.db.session import get_db
from app.modules.audit.service import record
from app.modules.billing.entitlements import ensure_capacity

router = APIRouter(prefix="/brands/{brand_id}", tags=["catalog"])


def _clean(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("must not be blank")
    return v


def _clean_aliases(v: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for a in v:
        a = " ".join(a.split())
        if a and a.lower() not in seen and len(a) <= 200:
            seen.add(a.lower())
            out.append(a)
    return out


# ---------------------------------------------------------------- schemas
Name = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_clean)]
Aliases = Annotated[list[str], Field(max_length=50), AfterValidator(_clean_aliases)]


class ProductIn(BaseModel):
    name: Name
    category: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    aliases: Aliases = Field(default_factory=list)


class ProductPatch(BaseModel):
    name: Name | None = None
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    aliases: Aliases | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    description: str
    aliases: list[str]
    created_at: datetime


class CompetitorIn(BaseModel):
    name: Name
    website: HttpUrl | None = None
    aliases: Aliases = Field(default_factory=list)
    notes: str = Field(default="", max_length=5000)


class CompetitorPatch(BaseModel):
    name: Name | None = None
    website: HttpUrl | None = None
    aliases: Aliases | None = None
    notes: str | None = Field(default=None, max_length=5000)


class CompetitorOut(BaseModel):
    id: uuid.UUID
    name: str
    website: str | None
    aliases: list[str]
    notes: str
    created_at: datetime


class KeywordIn(BaseModel):
    expression: Name
    kind: Literal["include", "exclude"] = "include"
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class KeywordOut(BaseModel):
    id: uuid.UUID
    expression: str
    kind: str
    language: str | None
    created_at: datetime


# ---------------------------------------------------------------- helpers
def _brand(db: Session, ctx: AuthContext, brand_id: uuid.UUID) -> Brand:
    b = db.scalars(select(Brand).where(Brand.id == brand_id,
                                       Brand.tenant_id == ctx.tenant_id)).one_or_none()
    if b is None:
        raise NotFound("Brand not found")
    return b


def _child(db: Session, model, brand: Brand, child_id: uuid.UUID, what: str):  # noqa: ANN001, ANN202
    obj = db.scalars(select(model).where(model.id == child_id, model.brand_id == brand.id,
                                         model.tenant_id == brand.tenant_id)).one_or_none()
    if obj is None:
        raise NotFound(f"{what} not found")
    return obj


def _flush(db: Session, what: str) -> None:
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict(f"{what} already exists", code="duplicate") from e


def _audit(db: Session, ctx: AuthContext, request: Request, action: str, brand: Brand,
           **meta: object) -> None:
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action=action,
           target_type="brand", target_id=brand.id, meta=meta, request=request)


def _product_out(p: Product) -> ProductOut:
    return ProductOut(id=p.id, name=p.name, category=p.category, description=p.description,
                      aliases=list(p.aliases or []), created_at=p.created_at)


def _competitor_out(c: Competitor) -> CompetitorOut:
    return CompetitorOut(id=c.id, name=c.name, website=c.website, aliases=list(c.aliases or []),
                         notes=c.notes, created_at=c.created_at)


def _keyword_out(k: BrandKeyword) -> KeywordOut:
    return KeywordOut(id=k.id, expression=k.expression, kind=k.kind, language=k.language,
                      created_at=k.created_at)


# ---------------------------------------------------------------- products
@router.get("/products", response_model=list[ProductOut])
def list_products(brand_id: uuid.UUID, ctx: AuthContext = Depends(require("brands:read")),
                  db: Session = Depends(get_db)) -> list[ProductOut]:
    b = _brand(db, ctx, brand_id)
    return [_product_out(p) for p in db.scalars(
        select(Product).where(Product.brand_id == b.id).order_by(Product.name))]


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(brand_id: uuid.UUID, body: ProductIn, request: Request,
                   ctx: AuthContext = Depends(require("brands:write")),
                   db: Session = Depends(get_db)) -> ProductOut:
    b = _brand(db, ctx, brand_id)
    p = Product(tenant_id=ctx.tenant_id, brand_id=b.id, **body.model_dump())
    db.add(p)
    _flush(db, "A product with this name")
    _audit(db, ctx, request, "product.created", b, name=p.name)
    db.commit()
    return _product_out(p)


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(brand_id: uuid.UUID, product_id: uuid.UUID, body: ProductPatch,
                   request: Request, ctx: AuthContext = Depends(require("brands:write")),
                   db: Session = Depends(get_db)) -> ProductOut:
    b = _brand(db, ctx, brand_id)
    p = _child(db, Product, b, product_id, "Product")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(p, k, v)
    p.updated_at = datetime.now(UTC)
    _flush(db, "A product with this name")
    _audit(db, ctx, request, "product.updated", b, product=p.name, fields=sorted(changes))
    db.commit()
    return _product_out(p)


@router.delete("/products/{product_id}", status_code=204)
def delete_product(brand_id: uuid.UUID, product_id: uuid.UUID, request: Request,
                   ctx: AuthContext = Depends(require("brands:write")),
                   db: Session = Depends(get_db)) -> Response:
    b = _brand(db, ctx, brand_id)
    p = _child(db, Product, b, product_id, "Product")
    db.delete(p)
    _audit(db, ctx, request, "product.deleted", b, name=p.name)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- competitors
@router.get("/competitors", response_model=list[CompetitorOut])
def list_competitors(brand_id: uuid.UUID, ctx: AuthContext = Depends(require("brands:read")),
                     db: Session = Depends(get_db)) -> list[CompetitorOut]:
    b = _brand(db, ctx, brand_id)
    return [_competitor_out(c) for c in db.scalars(
        select(Competitor).where(Competitor.brand_id == b.id).order_by(Competitor.name))]


@router.post("/competitors", response_model=CompetitorOut, status_code=201)
def create_competitor(brand_id: uuid.UUID, body: CompetitorIn, request: Request,
                      ctx: AuthContext = Depends(require("brands:write")),
                      db: Session = Depends(get_db)) -> CompetitorOut:
    b = _brand(db, ctx, brand_id)
    ensure_capacity(db, ctx.tenant_id, "max_competitors_per_brand", brand_id=b.id)
    data = body.model_dump()
    data["website"] = str(body.website) if body.website else None
    c = Competitor(tenant_id=ctx.tenant_id, brand_id=b.id, **data)
    db.add(c)
    _flush(db, "A competitor with this name")
    _audit(db, ctx, request, "competitor.created", b, name=c.name)
    db.commit()
    return _competitor_out(c)


@router.patch("/competitors/{competitor_id}", response_model=CompetitorOut)
def update_competitor(brand_id: uuid.UUID, competitor_id: uuid.UUID, body: CompetitorPatch,
                      request: Request, ctx: AuthContext = Depends(require("brands:write")),
                      db: Session = Depends(get_db)) -> CompetitorOut:
    b = _brand(db, ctx, brand_id)
    c = _child(db, Competitor, b, competitor_id, "Competitor")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if k == "website":
            v = str(v) if v else None
        setattr(c, k, v)
    c.updated_at = datetime.now(UTC)
    _flush(db, "A competitor with this name")
    _audit(db, ctx, request, "competitor.updated", b, competitor=c.name, fields=sorted(changes))
    db.commit()
    return _competitor_out(c)


@router.delete("/competitors/{competitor_id}", status_code=204)
def delete_competitor(brand_id: uuid.UUID, competitor_id: uuid.UUID, request: Request,
                      ctx: AuthContext = Depends(require("brands:write")),
                      db: Session = Depends(get_db)) -> Response:
    b = _brand(db, ctx, brand_id)
    c = _child(db, Competitor, b, competitor_id, "Competitor")
    db.delete(c)
    _audit(db, ctx, request, "competitor.deleted", b, name=c.name)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- keywords
@router.get("/keywords", response_model=list[KeywordOut])
def list_keywords(brand_id: uuid.UUID, ctx: AuthContext = Depends(require("brands:read")),
                  db: Session = Depends(get_db)) -> list[KeywordOut]:
    b = _brand(db, ctx, brand_id)
    return [_keyword_out(k) for k in db.scalars(
        select(BrandKeyword).where(BrandKeyword.brand_id == b.id)
        .order_by(BrandKeyword.kind, BrandKeyword.expression))]


@router.post("/keywords", response_model=KeywordOut, status_code=201)
def create_keyword(brand_id: uuid.UUID, body: KeywordIn, request: Request,
                   ctx: AuthContext = Depends(require("brands:write")),
                   db: Session = Depends(get_db)) -> KeywordOut:
    b = _brand(db, ctx, brand_id)
    ensure_capacity(db, ctx.tenant_id, "max_keywords_per_brand", brand_id=b.id)
    k = BrandKeyword(tenant_id=ctx.tenant_id, brand_id=b.id, **body.model_dump())
    db.add(k)
    _flush(db, "This keyword")
    _audit(db, ctx, request, "keyword.created", b, expression=k.expression, kind=k.kind)
    db.commit()
    return _keyword_out(k)


@router.delete("/keywords/{keyword_id}", status_code=204)
def delete_keyword(brand_id: uuid.UUID, keyword_id: uuid.UUID, request: Request,
                   ctx: AuthContext = Depends(require("brands:write")),
                   db: Session = Depends(get_db)) -> Response:
    b = _brand(db, ctx, brand_id)
    k = _child(db, BrandKeyword, b, keyword_id, "Keyword")
    db.delete(k)
    _audit(db, ctx, request, "keyword.deleted", b, expression=k.expression)
    db.commit()
    return Response(status_code=204)
