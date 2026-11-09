"""Manual invoicing (provider "manual"): drafts, issue with a gapless number and frozen
seller/buyer snapshots, payment recording, void, HTML/PDF rendering.

All calls run in the invoiced tenant's database context, by a platform administrator
(triggers refuse anyone else). Amounts are integers in minor units (centimes).

Legal note: Algerian invoices must carry mandatory mentions (seller and buyer RC, NIF, NIS,
article d'imposition, sequential number, date, amounts excl. and incl. tax, VAT, amount in
words…). The template includes them, but its compliance must be confirmed by a chartered
accountant before production use (flagged in docs/DEPLOYMENT.md).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.billing.words import amount_in_words
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import (
    BillingAccount,
    Invoice,
    InvoiceLine,
    Plan,
    PlanPrice,
    Subscription,
    Tenant,
)

PAYMENT_METHODS = ("bank_transfer", "ccp", "cheque", "cash", "card", "other")
DZ_REQUIRED = ("legal_name", "address", "rc", "nif", "nis", "article_imposition")


class InvoiceError(AppError):
    status_code = 422
    code = "invoice_invalid"


@dataclass
class LineIn:
    description: str
    quantity: Decimal
    unit_minor: int


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().analytics_timezone)).date()


def _lines(db: Session, inv: Invoice) -> list[InvoiceLine]:
    return list(db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
                           .order_by(InvoiceLine.position)))


def set_lines(db: Session, inv: Invoice, lines: list[LineIn], *, tax_rate: Decimal,
              stamp_duty_minor: int = 0) -> None:
    if inv.status != "draft":
        raise InvoiceError("Only drafts can be edited")
    if not lines:
        raise InvoiceError("An invoice needs at least one line")
    for old in _lines(db, inv):
        db.delete(old)
    db.flush()
    subtotal = 0
    for i, ln in enumerate(lines, start=1):
        amount = _round(ln.quantity * ln.unit_minor)
        subtotal += amount
        db.add(InvoiceLine(id=uuid.uuid4(), tenant_id=inv.tenant_id, invoice_id=inv.id,
                           position=i, description=ln.description.strip()[:500],
                           quantity=float(ln.quantity), unit_minor=ln.unit_minor,
                           amount_minor=amount))
    inv.subtotal_minor = subtotal
    inv.tax_rate = float(tax_rate)
    inv.tax_minor = _round(Decimal(subtotal) * tax_rate / 100)
    inv.stamp_duty_minor = stamp_duty_minor
    inv.total_minor = subtotal + inv.tax_minor + stamp_duty_minor
    db.flush()


def create_draft(db: Session, tenant_id: uuid.UUID, *, currency: str, lines: list[LineIn],
                 tax_rate: Decimal | None = None, locale: str = "fr",
                 period: tuple[date, date] | None = None, notes: str = "",
                 created_by: uuid.UUID | None = None) -> Invoice:
    s = get_settings()
    inv = Invoice(id=uuid.uuid4(), tenant_id=tenant_id, status="draft", currency=currency,
                  locale=locale, notes=notes, created_by=created_by,
                  period_start=period[0] if period else None,
                  period_end=period[1] if period else None)
    db.add(inv)
    db.flush()
    set_lines(db, inv, lines,
              tax_rate=tax_rate if tax_rate is not None else Decimal(str(s.billing_default_tax_rate)))
    return inv


def draft_from_subscription(db: Session, tenant_id: uuid.UUID, created_by: uuid.UUID | None
                            ) -> Invoice:
    """One line for the current plan at its active price for the subscription's currency
    and interval. Fails clearly if the administrator has not set that price."""
    sub = db.scalars(select(Subscription).where(Subscription.tenant_id == tenant_id)).one()
    plan = db.get(Plan, sub.plan_id)
    acct = db.get(BillingAccount, tenant_id)
    currency = sub.currency or (acct.currency if acct else "DZD")
    interval = sub.billing_interval or "month"
    price = db.scalars(select(PlanPrice).where(
        PlanPrice.plan_id == sub.plan_id, PlanPrice.currency == currency,
        PlanPrice.billing_interval == interval, PlanPrice.is_active)).one_or_none()
    if price is None:
        raise InvoiceError(f"No active {interval} price in {currency} for plan {plan.key}")
    start = (sub.current_period_start or datetime.now(UTC)).date()
    end = (sub.current_period_end.date() if sub.current_period_end else
           start + (timedelta(days=365) if interval == "year" else timedelta(days=30)))
    label = {"month": "mensuel", "year": "annuel"}[interval]
    return create_draft(
        db, tenant_id, currency=currency, created_by=created_by, period=(start, end),
        lines=[LineIn(f"Abonnement {plan.name} ({label}) — du {start:%d/%m/%Y} au {end:%d/%m/%Y}",
                      Decimal(1), price.amount_minor)])


def seller_snapshot() -> dict[str, str]:
    s = get_settings()
    return {"name": s.billing_seller_name, "address": s.billing_seller_address,
            "rc": s.billing_seller_rc, "nif": s.billing_seller_nif, "nis": s.billing_seller_nis,
            "article_imposition": s.billing_seller_article_imposition,
            "bank": s.billing_seller_bank, "email": s.billing_seller_email,
            "phone": s.billing_seller_phone}


def missing_buyer_fields(acct: BillingAccount | None) -> list[str]:
    if acct is None:
        return list(DZ_REQUIRED)
    required = DZ_REQUIRED if acct.country == "DZ" else ("legal_name", "address")
    return [f for f in required if not (getattr(acct, f) or "").strip()]


def issue(db: Session, inv: Invoice) -> Invoice:
    s = get_settings()
    if inv.status != "draft":
        raise InvoiceError("Already issued")
    if not _lines(db, inv):
        raise InvoiceError("An invoice needs at least one line")
    seller = seller_snapshot()
    if not seller["name"] or not seller["nif"]:
        raise InvoiceError("Seller identity is not configured (REPINTEL_BILLING_SELLER_*)",
                           code="seller_not_configured")
    acct = db.get(BillingAccount, inv.tenant_id)
    if missing := missing_buyer_fields(acct):
        raise InvoiceError(f"Customer billing details incomplete: {', '.join(missing)}",
                           code="billing_account_incomplete")
    assert acct is not None
    tenant = db.get(Tenant, inv.tenant_id)
    inv.buyer = {k: getattr(acct, k) for k in (
        "legal_name", "address", "city", "postal_code", "country", "email", "phone", "rc",
        "nif", "nis", "article_imposition", "vat_number")}
    inv.buyer["organization"] = tenant.name if tenant else ""
    inv.seller = seller
    inv.number = db.execute(text("SELECT next_invoice_number(:p)"),
                            {"p": s.billing_invoice_prefix}).scalar_one()
    inv.issue_date = _today()
    inv.due_date = inv.issue_date + timedelta(days=s.billing_payment_terms_days)
    inv.amount_in_words = amount_in_words(inv.total_minor, inv.currency)
    inv.status = "issued"
    db.flush()
    return inv


def mark_paid(db: Session, inv: Invoice, *, method: str, reference: str,
              paid_at: datetime | None) -> None:
    if inv.status != "issued":
        raise InvoiceError("Only issued invoices can be marked paid")
    if method not in PAYMENT_METHODS:
        raise InvoiceError("Unknown payment method")
    inv.status, inv.payment_method = "paid", method
    inv.payment_reference, inv.paid_at = reference[:200], paid_at or datetime.now(UTC)
    db.flush()


def void(db: Session, inv: Invoice, reason: str) -> None:
    if inv.status == "draft":
        db.delete(inv)
        db.flush()
        return
    if inv.status == "void":
        raise InvoiceError("Already void")
    inv.status, inv.void_reason, inv.voided_at = "void", reason[:300], datetime.now(UTC)
    db.flush()


# ------------------------------------------------------------------------------ render
L = {
    "fr": {"invoice": "Facture", "draft": "Brouillon — sans valeur", "number": "N°",
           "date": "Date", "due": "Échéance", "period": "Période", "seller": "Prestataire",
           "buyer": "Client", "desc": "Désignation", "qty": "Qté", "unit": "Prix unitaire HT",
           "amount": "Montant HT", "subtotal": "Total HT", "tax": "TVA", "stamp": "Droit de timbre",
           "total": "Total TTC", "words": "Arrêtée la présente facture à la somme de :",
           "paid": "Payée le", "void": "ANNULÉE", "payment": "Règlement", "bank": "Coordonnées bancaires"},
    "en": {"invoice": "Invoice", "draft": "Draft — not valid", "number": "No.",
           "date": "Date", "due": "Due", "period": "Period", "seller": "Supplier",
           "buyer": "Customer", "desc": "Description", "qty": "Qty", "unit": "Unit price (excl. tax)",
           "amount": "Amount (excl. tax)", "subtotal": "Total excl. tax", "tax": "VAT",
           "stamp": "Stamp duty", "total": "Total incl. tax",
           "words": "Amount in words (French):", "paid": "Paid on", "void": "VOID",
           "payment": "Payment", "bank": "Bank details"},
    "ar": {"invoice": "فاتورة", "draft": "مسودة — غير صالحة", "number": "رقم",
           "date": "التاريخ", "due": "تاريخ الاستحقاق", "period": "الفترة", "seller": "مقدم الخدمة",
           "buyer": "الزبون", "desc": "البيان", "qty": "الكمية", "unit": "سعر الوحدة دون رسوم",
           "amount": "المبلغ دون رسوم", "subtotal": "المجموع دون رسوم", "tax": "الرسم على القيمة المضافة",
           "stamp": "حقوق الطابع", "total": "المجموع بكل الرسوم",
           "words": "أوقفت هذه الفاتورة على مبلغ (بالفرنسية):", "paid": "دُفعت في", "void": "ملغاة",
           "payment": "الدفع", "bank": "المعلومات البنكية"},
}
IDS = (("rc", "RC"), ("nif", "NIF"), ("nis", "NIS"), ("article_imposition", "AI"),
       ("vat_number", "VAT"))


def money(minor: int, currency: str) -> str:
    units, cents = divmod(abs(minor), 100)
    grouped = f"{units:,}".replace(",", " ")
    return f"{'-' if minor < 0 else ''}{grouped},{cents:02d} {currency}"


def _party(p: dict[str, Any], title: str) -> str:
    ids = " · ".join(f"{label} : {escape(str(p[k]))}" for k, label in IDS if p.get(k))
    addr = ", ".join(escape(str(p[k])) for k in ("address", "postal_code", "city", "country")
                     if p.get(k))
    contact = " · ".join(escape(str(p[k])) for k in ("email", "phone") if p.get(k))
    name = escape(str(p.get("legal_name") or p.get("name") or ""))
    return (f'<div class="party"><h3>{escape(title)}</h3><p><strong>{name}</strong></p>'
            f"<p>{addr}</p><p>{ids}</p><p>{contact}</p></div>")


def render_html(db: Session, inv: Invoice) -> str:
    loc = inv.locale if inv.locale in L else "fr"
    t = L[loc]
    d = "rtl" if loc == "ar" else "ltr"
    seller = inv.seller or seller_snapshot()
    buyer = inv.buyer
    if not buyer:
        acct = db.get(BillingAccount, inv.tenant_id)
        buyer = {k: getattr(acct, k) for k in ("legal_name", "address", "city", "country",
                                               "rc", "nif", "nis", "article_imposition",
                                               "vat_number", "email")} if acct else {}
    rows = "".join(
        f"<tr><td>{escape(ln.description)}</td><td class='n'>{ln.quantity:g}</td>"
        f"<td class='n'>{money(ln.unit_minor, inv.currency)}</td>"
        f"<td class='n'>{money(ln.amount_minor, inv.currency)}</td></tr>"
        for ln in _lines(db, inv))
    status = {"draft": t["draft"], "void": t["void"]}.get(inv.status, "")
    paid = (f"<p>{t['paid']} {inv.paid_at:%d/%m/%Y} — {escape(inv.payment_method or '')} "
            f"{escape(inv.payment_reference)}</p>" if inv.status == "paid" and inv.paid_at else "")
    period = (f"<p>{t['period']} : {inv.period_start:%d/%m/%Y} → {inv.period_end:%d/%m/%Y}</p>"
              if inv.period_start and inv.period_end else "")
    stamp = (f"<tr><td>{t['stamp']}</td><td class='n'>{money(inv.stamp_duty_minor, inv.currency)}"
             f"</td></tr>" if inv.stamp_duty_minor else "")
    words = (f"<p class='words'>{t['words']} <strong>{escape(inv.amount_in_words)}</strong></p>"
             if inv.amount_in_words else "")
    bank = (f"<p><strong>{t['bank']} :</strong> {escape(seller.get('bank', ''))}</p>"
            if seller.get("bank") else "")
    idate = f"{inv.issue_date:%d/%m/%Y}" if inv.issue_date else "—"
    ddate = f"{inv.due_date:%d/%m/%Y}" if inv.due_date else "—"
    return f"""<!doctype html><html lang="{loc}" dir="{d}"><head><meta charset="utf-8">
<title>{t['invoice']} {escape(inv.number or '')}</title><style>
@page {{ size: A4; margin: 16mm 14mm; }}
body {{ font-family: "DejaVu Sans", "Noto Sans", Arial, sans-serif; font-size: 11px; color: #111; }}
h1 {{ font-size: 22px; margin: 0; }} h3 {{ font-size: 11px; text-transform: uppercase; color: #555; margin: 0 0 4px; }}
.head {{ display: flex; justify-content: space-between; align-items: flex-start; }}
.parties {{ display: flex; gap: 24px; margin: 20px 0; }} .party {{ flex: 1; border: 1px solid #ddd; padding: 10px; }}
.party p {{ margin: 2px 0; }} table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 6px; text-align: start; }} th {{ background: #f3f3f3; }}
.n {{ text-align: end; white-space: nowrap; }} .totals {{ width: 45%; margin-inline-start: auto; margin-top: 10px; }}
.totals tr:last-child td {{ font-weight: bold; font-size: 13px; }} .status {{ color: #b00; font-weight: bold; font-size: 16px; }}
.words {{ margin-top: 14px; }} .notes {{ margin-top: 14px; color: #444; }}
</style></head><body>
<div class="head"><div><h1>{t['invoice']}</h1>
<p>{t['number']} <strong>{escape(inv.number or '—')}</strong></p>
<p>{t['date']} : {idate}</p>
<p>{t['due']} : {ddate}</p>{period}</div>
<div class="status">{escape(status)}</div></div>
<div class="parties">{_party(seller, t['seller'])}{_party(buyer, t['buyer'])}</div>
<table><thead><tr><th>{t['desc']}</th><th class='n'>{t['qty']}</th><th class='n'>{t['unit']}</th>
<th class='n'>{t['amount']}</th></tr></thead><tbody>{rows}</tbody></table>
<table class="totals"><tbody>
<tr><td>{t['subtotal']}</td><td class='n'>{money(inv.subtotal_minor, inv.currency)}</td></tr>
<tr><td>{t['tax']} ({inv.tax_rate:g} %)</td><td class='n'>{money(inv.tax_minor, inv.currency)}</td></tr>
{stamp}<tr><td>{t['total']}</td><td class='n'>{money(inv.total_minor, inv.currency)}</td></tr>
</tbody></table>{words}{paid}{bank}
<p class="notes">{escape(inv.notes)}</p></body></html>"""


def render_pdf(db: Session, inv: Invoice) -> bytes:
    from app.reports.formats import html_to_pdf
    return html_to_pdf(render_html(db, inv))
