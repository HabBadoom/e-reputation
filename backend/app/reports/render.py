"""HTML rendering (also the source of the PDF). Every value from the database is escaped; the
page has no scripts and no external resources (fonts are the system's)."""

from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.reports.i18n import tr

COLORS = {"positive": "#2f7d4f", "negative": "#b3261e", "neutral": "#8a9a96",
          "other": "#d9d6cc"}


def _d(v: datetime | None, with_time: bool = False) -> str:
    if v is None:
        return ""
    v = v.astimezone(ZoneInfo(get_settings().analytics_timezone))
    return v.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def _n(v: float | int | None, suffix: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v}{suffix}"


def chart_svg(series: list[dict[str, Any]], loc: str, width: int = 700, height: int = 220) -> str:
    """Stacked daily bars: negative at the bottom (most important), then positive, neutral/mixed,
    not analysed/uncertain."""
    if not series:
        return ""
    pad_l, pad_b, pad_t = 34, 26, 8
    w, h = width - pad_l - 8, height - pad_b - pad_t
    peak = max(1, max(r["total"] for r in series))
    step = w / len(series)
    bar = max(1.0, step * 0.72)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" role="img" direction="ltr">']
    for frac in (0, 0.5, 1):
        y = pad_t + h - h * frac
        parts.append(f'<line x1="{pad_l}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="#e6e3da" stroke-width="1"/>'
                     f'<text x="{pad_l - 6}" y="{y + 4:.1f}" font-size="10" fill="#6b7d79" '
                     f'text-anchor="end">{round(peak * frac)}</text>')
    for i, r in enumerate(series):
        x = pad_l + i * step + (step - bar) / 2
        y = pad_t + h
        other = r["total"] - r["positive"] - r["negative"] - r["neutral"] - r["mixed"]
        for key, val in (("negative", r["negative"]), ("positive", r["positive"]),
                         ("neutral", r["neutral"] + r["mixed"]), ("other", other)):
            if val <= 0:
                continue
            bh = h * val / peak
            y -= bh
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar:.1f}" height="{bh:.1f}" '
                         f'fill="{COLORS[key]}"/>')
    n = len(series)
    for i in sorted({0, n // 2, n - 1}):
        x = pad_l + i * step + step / 2
        anchor = "middle"
        if i == 0:
            x, anchor = pad_l, "start"
        elif i == n - 1:
            x, anchor = width - 8, "end"
        parts.append(f'<text x="{x:.1f}" y="{height - 8}" font-size="10" fill="#6b7d79" '
                     f'text-anchor="{anchor}">{escape(str(series[i]["day"]))}</text>')
    parts.append("</svg>")
    legend = "".join(
        f'<span class="lg"><i style="background:{COLORS[k]}"></i>{escape(tr(loc, "legend." + k))}'
        f"</span>" for k in ("negative", "positive", "neutral", "other"))
    return "".join(parts) + f'<div class="legend">{legend}</div>'


def _kpi(label: str, value: str, sub: str = "") -> str:
    return (f'<div class="kpi"><div class="kl">{escape(label)}</div>'
            f'<div class="kv">{escape(value)}</div><div class="ks">{escape(sub)}</div></div>')


def _mentions(rows: list[dict[str, Any]], loc: str) -> str:
    if not rows:
        return f'<p class="muted">{escape(tr(loc, "none"))}</p>'
    out = []
    for m in rows:
        date = _d(m["published_at"]) if m["published_at"] else tr(loc, "undated")
        excerpt = (m["original_text"] or "")[:400]
        out.append(
            f'<div class="mention"><div class="mt">{escape(m["title"] or m["domain"])}</div>'
            f'<div class="mx" dir="auto">{escape(excerpt)}</div>'
            f'<div class="mm">{escape(m["domain"])} · {escape(date)} · '
            f'{escape(tr(loc, "seen", n=m["seen_count"]))} · '
            f'<span class="url" dir="ltr">{escape(m["url"][:120])}</span></div></div>')
    return "".join(out)


def _table(head: list[str], rows: list[list[str]], loc: str) -> str:
    if not rows:
        return f'<p class="muted">{escape(tr(loc, "none"))}</p>'
    th = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "DejaVu Sans", "Noto Sans", "Noto Naskh Arabic", Arial, sans-serif;
       color: #0f1f1c; font-size: 11.5px; line-height: 1.45; margin: 0; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 15px; margin: 22px 0 8px;
  padding-bottom: 4px; border-bottom: 2px solid #0d6b5c; break-after: avoid; }
.meta { color: #50625e; font-size: 11px; } .muted { color: #6b7d79; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.kpi { border: 1px solid #dcdad2; border-radius: 6px; padding: 8px 10px; break-inside: avoid; }
.kl { font-size: 10px; color: #50625e; text-transform: uppercase; letter-spacing: .03em; }
.kv { font-size: 20px; font-weight: bold; margin-top: 2px; } .ks { font-size: 10px; color: #6b7d79; }
table { width: 100%; border-collapse: collapse; font-size: 11px; }
th, td { text-align: start; padding: 5px 6px; border-bottom: 1px solid #ecebe6; vertical-align: top; }
th { background: #f6f5f1; font-weight: bold; }
.mention { border-inline-start: 3px solid #dcdad2; padding: 4px 10px; margin: 0 0 8px; break-inside: avoid; }
.mt { font-weight: bold; } .mx { margin: 2px 0; } .mm { font-size: 10px; color: #6b7d79; }
.url { word-break: break-all; }
.legend { margin-top: 4px; font-size: 10px; color: #50625e; }
.lg { margin-inline-end: 12px; } .lg i { display: inline-block; width: 9px; height: 9px;
  margin-inline-end: 4px; vertical-align: middle; }
ul { padding-inline-start: 18px; margin: 0; } li { margin-bottom: 4px; }
.sev { font-weight: bold; }
"""


def render_html(d: dict[str, Any]) -> str:
    loc = d["locale"]
    s = get_settings()
    t, p = d["totals"], d["previous"]
    rep, prep = d["reputation"], d["previous_reputation"]
    direction = "rtl" if loc == "ar" else "ltr"

    rep_value = _n(rep["value"]) if rep["value"] is not None else "—"
    rep_sub = (tr(loc, "vs_previous", v=_n(prep["value"])) if rep["value"] is not None
               else tr(loc, "not_enough", n=rep["sample"], min=s.reputation_min_sample))
    kpis = "".join([
        _kpi(tr(loc, "kpi.total"), _n(t["total"]), tr(loc, "vs_previous", v=p["total"])),
        _kpi(tr(loc, "kpi.negative"), _n(t["negative"]),
             tr(loc, "vs_previous", v=p["negative"])),
        _kpi(tr(loc, "kpi.positive"), _n(t["positive"]),
             tr(loc, "vs_previous", v=p["positive"])),
        _kpi(tr(loc, "kpi.reputation"), rep_value, rep_sub),
        _kpi(tr(loc, "kpi.negative_share"), _n(d["negative_share"], " %"),
             tr(loc, "vs_previous", v=_n(d["previous_negative_share"], " %"))),
        _kpi(tr(loc, "kpi.neutral"), _n(t["neutral"] + t["mixed"])),
        _kpi(tr(loc, "kpi.sources"), _n(t["domains"])),
    ])

    ent_rows = [[e["entity_name"], str(e["total"]), _n(round(100 * e["share"], 1), " %"),
                 str(e["negative"]), _n(e["reputation"]["value"])]
                for e in d["brands"] + d["competitors"]]
    prod_rows = [[e["entity_name"], str(e["total"]), str(e["negative"]),
                  _n(e["reputation"]["value"])] for e in d["products"]]
    topic_rows = [[x["topic"], str(x["day"]), str(x["count"]), _n(float(x["expected"])),
                   _n(float(x["z"]))] + ([x["brand"]] if d["brand"] is None else [])
                  for x in d["topics"]]
    crisis_rows = [[c["brand"], c["severity"], tr(loc, "status." + c["status"]),
                    _d(c["started_at"], True), _d(c["resolved_at"], True) or "—",
                    str(c["peak_negative"])] for c in d["crises"]]
    h = d["health"]
    limits = [tr(loc, "lim.coverage"),
              tr(loc, "lim.undated", n=t["undated"]),
              tr(loc, "lim.unanalysed", n=t["unanalysed"], u=t["uncertain"]),
              tr(loc, "lim.sources", n=h["sources"], f=h["failing_sources"], r=h["failed_runs"]),
              tr(loc, "lim.ai")]
    recs = [tr(loc, "rec." + code, **vals) for code, vals in d["recommendations"]]

    sections = [
        f'<h2>{escape(tr(loc, "summary"))}</h2><div class="kpis">{kpis}</div>',
        f'<h2>{escape(tr(loc, "recommendations"))}</h2><ul>'
        + "".join(f"<li>{escape(r)}</li>" for r in recs) + "</ul>",
        f'<h2>{escape(tr(loc, "evolution"))}</h2>{chart_svg(d["series"], loc)}',
    ]
    if d["crises"] or d["kind"] == "crisis":
        sections.append(f'<h2>{escape(tr(loc, "crises"))}</h2>' + _table(
            [tr(loc, "entity"), tr(loc, "severity"), tr(loc, "status"), tr(loc, "started"),
             tr(loc, "resolved"), tr(loc, "peak")], crisis_rows, loc))
    comp = (f'<h2>{escape(tr(loc, "competition"))}</h2>' + _table(
        [tr(loc, "entity"), tr(loc, "mentions"), tr(loc, "share"), tr(loc, "negative"),
         tr(loc, "reputation")], ent_rows, loc))
    if d["products"]:
        comp += f'<h2>{escape(tr(loc, "products"))}</h2>' + _table(
            [tr(loc, "entity"), tr(loc, "mentions"), tr(loc, "negative"),
             tr(loc, "reputation")], prod_rows, loc)
    mentions = (f'<h2>{escape(tr(loc, "top_negative"))}</h2>'
                + _mentions(d["top_negative"], loc)
                + f'<h2>{escape(tr(loc, "top_positive"))}</h2>'
                + _mentions(d["top_positive"], loc))
    topics = f'<h2>{escape(tr(loc, "topics"))}</h2>' + _table(
        [tr(loc, "topic"), tr(loc, "day"), tr(loc, "count"), tr(loc, "expected"), "z"]
        + ([tr(loc, "entity")] if d["brand"] is None else []), topic_rows, loc)
    if d["kind"] == "competitor":
        sections += [comp, mentions, topics]
    else:
        sections += [mentions, topics, comp]
    sections += [
        f'<h2>{escape(tr(loc, "limitations"))}</h2><ul>'
        + "".join(f"<li>{escape(x)}</li>" for x in limits) + "</ul>",
        f'<h2>{escape(tr(loc, "methodology"))}</h2><p>'
        + escape(tr(loc, "meth.body", min=s.reputation_min_sample, days=s.trend_baseline_days))
        + "</p>",
    ]
    title = tr(loc, "title." + d["kind"])
    subject = d["brand"] or tr(loc, "all_brands")
    c = " : " if loc == "fr" else ": "
    meta = (f'{escape(tr(loc, "organization"))}{c}{escape(d["organization"])} · '
            f'{escape(tr(loc, "period"))}{c}<span dir="ltr">{_d(d["period_start"], True)} → '
            f'{_d(d["period_end"], True)}</span> ({escape(d["timezone"])}) · '
            f'{escape(tr(loc, "generated"))}{c}<span dir="ltr">{_d(d["generated_at"], True)}'
            f"</span>")
    return (f'<!doctype html><html lang="{loc}" dir="{direction}"><head><meta charset="utf-8">'
            f"<title>{escape(title)} — {escape(subject)}</title><style>{CSS}</style></head>"
            f"<body><h1>{escape(title)} — {escape(subject)}</h1>"
            f'<div class="meta">{meta}</div>{"".join(sections)}</body></html>')
