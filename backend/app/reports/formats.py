"""Output formats and the `report.generate` job body."""

import csv
import io
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Report
from app.reports.data import build
from app.reports.i18n import tr
from app.reports.render import render_html

log = logging.getLogger("repintel.reports")

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class ReportRenderError(Exception):
    pass


def html_to_pdf(html: str) -> bytes:
    """Headless Chromium with JavaScript disabled and every network request refused: the page
    is self-contained, so nothing can be fetched even if content were somehow injected."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise ReportRenderError("playwright is not installed") from e
    timeout = get_settings().report_render_timeout_seconds * 1000
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            ctx = browser.new_context(java_script_enabled=False, offline=True)
            ctx.route("**/*", lambda route: route.abort())
            page = ctx.new_page()
            page.set_default_timeout(timeout)
            page.set_content(html, wait_until="load")
            return page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()


def _cell(v: Any) -> Any:
    """Neutralise spreadsheet formulas in text coming from the web (CSV/XLSX injection)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    if isinstance(v, datetime):
        return v.astimezone(UTC).replace(tzinfo=None)
    return v


def to_csv(d: dict[str, Any]) -> bytes:
    loc = d["locale"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["day", "total", "positive", "negative", "neutral", "mixed", "uncertain",
                "unanalysed"])
    for r in d["series"]:
        w.writerow([r["day"], r["total"], r["positive"], r["negative"], r["neutral"], r["mixed"],
                    r["uncertain"], r["unanalysed"]])
    w.writerow([])
    w.writerow([tr(loc, "limitations")])
    w.writerow([_cell(tr(loc, "lim.coverage"))])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM: Excel opens UTF-8 correctly


def to_xlsx(d: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    loc = d["locale"]
    wb = Workbook()
    bold = Font(bold=True)

    def sheet(title: str, head: list[str], rows: list[list[Any]], first: bool = False) -> None:
        ws = wb.active if first else wb.create_sheet()
        ws.title = title[:31]
        if loc == "ar":
            ws.sheet_view.rightToLeft = True
        ws.append(head)
        for c in ws[1]:
            c.font = bold
        for r in rows:
            ws.append([_cell(v) for v in r])
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(60, max(10, width + 2))

    t, rep = d["totals"], d["reputation"]
    sheet(tr(loc, "summary"), ["", ""], [
        [tr(loc, "organization"), d["organization"]],
        ["brand", d["brand"] or tr(loc, "all_brands")],
        [tr(loc, "period"),
         f"{d['period_start']:%Y-%m-%d %H:%M} → {d['period_end']:%Y-%m-%d %H:%M} UTC"],
        [tr(loc, "kpi.total"), t["total"]], [tr(loc, "kpi.positive"), t["positive"]],
        [tr(loc, "kpi.negative"), t["negative"]],
        [tr(loc, "kpi.neutral"), t["neutral"] + t["mixed"]],
        [tr(loc, "kpi.reputation"), rep["value"]],
        [tr(loc, "kpi.negative_share"), d["negative_share"]],
        [tr(loc, "kpi.sources"), t["domains"]],
    ] + [[tr(loc, "recommendations"), tr(loc, "rec." + c, **v)] for c, v in d["recommendations"]]
      + [[tr(loc, "limitations"), tr(loc, "lim.coverage")]], first=True)
    sheet(tr(loc, "evolution"), ["day", "total", "positive", "negative", "neutral", "mixed",
                                 "uncertain", "unanalysed"],
          [[r["day"], r["total"], r["positive"], r["negative"], r["neutral"], r["mixed"],
            r["uncertain"], r["unanalysed"]] for r in d["series"]])
    sheet(tr(loc, "competition"), [tr(loc, "entity"), "type", tr(loc, "mentions"),
                                   tr(loc, "share"), "positive", "negative",
                                   tr(loc, "reputation")],
          [[e["entity_name"], e["entity_type"], e["total"], e.get("share"), e["positive"],
            e["negative"], e["reputation"]["value"]]
           for e in d["brands"] + d["competitors"] + d["products"]])
    rows = []
    for sentiment, items in (("negative", d["top_negative"]), ("positive", d["top_positive"])):
        for m in items:
            rows.append([sentiment, m["title"], m["original_text"][:2000], m["domain"], m["url"],
                         m["published_at"], m["seen_count"]])
    sheet(tr(loc, "top_negative"), ["sentiment", "title", "excerpt", "domain", "url",
                                    "published_at (UTC)", "seen"], rows)
    sheet(tr(loc, "topics"), [tr(loc, "topic"), tr(loc, "day"), tr(loc, "count"),
                              tr(loc, "expected"), "z", "brand"],
          [[x["topic"], x["day"], x["count"], float(x["expected"]), float(x["z"]), x["brand"]]
           for x in d["topics"]])
    sheet(tr(loc, "crises"), ["brand", tr(loc, "severity"), tr(loc, "status"),
                              "started (UTC)", "resolved (UTC)", tr(loc, "peak")],
          [[c["brand"], c["severity"], c["status"], c["started_at"], c["resolved_at"],
            c["peak_negative"]] for c in d["crises"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[:40] or "report"


def generate(db: Session, report: Report) -> None:
    report.status = "running"
    db.flush()
    d = build(db, report)
    if report.format == "html":
        content = render_html(d).encode("utf-8")
    elif report.format == "pdf":
        content = html_to_pdf(render_html(d))
    elif report.format == "xlsx":
        content = to_xlsx(d)
    else:
        content = to_csv(d)
    if len(content) > get_settings().report_max_bytes:
        raise ReportRenderError("report too large")
    name = _slug(d["brand"] or d["organization"])
    report.content = content
    report.content_type = CONTENT_TYPES[report.format]
    report.filename = f"{name}-{report.kind}-{d['period_end']:%Y%m%d}.{report.format}"
    report.size_bytes = len(content)
    report.status, report.error = "ready", None
    report.finished_at = datetime.now(UTC)
