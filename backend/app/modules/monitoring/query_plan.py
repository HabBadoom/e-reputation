"""Turn a brand's catalog into the search expressions a monitoring run will use.

Deterministic, provider-neutral and explainable: the UI shows exactly what will be searched.
Connectors (Milestone 3) translate each group into their own query syntax.
"""

from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Brand, BrandAlias, BrandKeyword, Competitor, MonitoringProject, Product

MAX_TERMS_PER_GROUP = 40


@dataclass
class QueryGroup:
    entity_type: str            # brand | product | competitor
    entity_name: str
    terms: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def as_search_string(self) -> str:
        """Generic boolean form ("a" OR "b" -"c"); individual providers may differ."""
        inc = " OR ".join(f'"{t}"' for t in self.terms)
        exc = " ".join(f'-"{t}"' for t in self.exclude)
        return f"({inc}) {exc}".strip() if self.terms else ""

    def to_dict(self) -> dict:
        return {**asdict(self), "search_string": self.as_search_string()}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        k = " ".join(i.split()).casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(" ".join(i.split()))
    return out[:MAX_TERMS_PER_GROUP]


def build_query_plan(*, brand_name: str, brand_aliases: list[str],
                     products: list[tuple[str, list[str]]],
                     competitors: list[tuple[str, list[str]]],
                     include_keywords: list[str], exclude_keywords: list[str]
                     ) -> list[QueryGroup]:
    exclude = _dedupe(exclude_keywords)
    groups = [QueryGroup("brand", brand_name,
                         _dedupe([brand_name, *brand_aliases, *include_keywords]), exclude)]
    for name, aliases in products:
        groups.append(QueryGroup("product", name, _dedupe([name, *aliases]), exclude))
    for name, aliases in competitors:
        groups.append(QueryGroup("competitor", name, _dedupe([name, *aliases]), exclude))
    return groups


def load_query_plan(db: Session, project: MonitoringProject) -> list[QueryGroup]:
    brand = db.get(Brand, project.brand_id)
    assert brand is not None

    def by_brand(model):  # noqa: ANN001, ANN202
        return select(model).where(model.brand_id == brand.id,
                                   model.tenant_id == project.tenant_id)

    aliases = [a.alias for a in db.scalars(by_brand(BrandAlias).order_by(BrandAlias.created_at))]
    products = [(p.name, list(p.aliases or [])) for p in db.scalars(
        by_brand(Product).order_by(Product.name))]
    competitors = [(c.name, list(c.aliases or [])) for c in db.scalars(
        by_brand(Competitor).order_by(Competitor.name))] if project.include_competitors else []
    kws = list(db.scalars(by_brand(BrandKeyword).order_by(BrandKeyword.created_at)))
    return build_query_plan(
        brand_name=brand.name, brand_aliases=aliases, products=products,
        competitors=competitors,
        include_keywords=[k.expression for k in kws if k.kind == "include"],
        exclude_keywords=[k.expression for k in kws if k.kind == "exclude"],
    )

