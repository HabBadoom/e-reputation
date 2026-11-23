"""Extraction benchmark: run vision models on a folder of screenshots and write a report.

    python -m app.scanner.extraction.bench /captures --model <model-a> --model <model-b> \
        [--limit 20] [--price <model-a>=2,10] [--workers 4]

For each model and image: the same sanitising as an upload, the same prompt and schema as
production, then the entries as Scanner would store them. Writes, next to the images:
  rapport-extraction/index.html     side-by-side view (image | entries), totals, cost
  rapport-extraction/<model>/*.json raw model output per image
If `attendu/<image>.json` exists ({"entries": [{"author": "...", "text": "..."}]}), the entries
are scored against it (found, missed, extra; author and text exactness; character error rate).
No database is used and nothing is stored in Scanner.
"""

import argparse
import difflib
import html
import json
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from app.core.config import get_settings
from app.nlp.llm import LLMError
from app.scanner import images
from app.scanner.extraction import dates, schema, text, vision

EXTS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class Run:
    image: str
    model: str
    ok: bool = False
    error: str = ""
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    parts: int = 1
    result: schema.Result | None = None
    captured: datetime | None = None
    score: dict = field(default_factory=dict)


def _light(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s or "").split())


def _cer(a: str, b: str, *, exact: bool = False) -> float:
    """Character error rate of `a` against reference `b`. `exact`: every letter counts
    (ة/ه, hamza, punctuation); otherwise on the comparison form, used to pair entries."""
    a, b = (_light(a), _light(b)) if exact else (text.normalise(a), text.normalise(b))
    if not b:
        return 0.0 if not a else 1.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    edits = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")
    return min(1.0, edits / len(b))


def score(got: list[schema.Entry], expected: list[dict]) -> dict:
    """Greedy matching on text similarity; counts found / missed / extra entries."""
    pool = list(range(len(got)))
    found, cers, authors_ok = 0, [], 0
    for exp in expected:
        etext = (exp.get("text") or "") + " " + (exp.get("image_text") or "")
        best, best_cer = None, 1.0
        for i in pool:
            c = _cer(f"{got[i].text} {got[i].image_text}", etext)
            if c < best_cer:
                best, best_cer = i, c
        if best is not None and best_cer <= 0.5:
            pool.remove(best)
            found += 1
            cers.append(_cer(f"{got[best].text} {got[best].image_text}", etext, exact=True))
            if text.normalise(got[best].author) == text.normalise(exp.get("author") or ""):
                authors_ok += 1
    return {"expected": len(expected), "found": found, "missed": len(expected) - found,
            "extra": len(pool), "authors_exact": authors_ok,
            "cer": round(sum(cers) / len(cers), 4) if cers else None,
            "texts_exact": sum(1 for c in cers if c == 0.0)}


def run_one(path: Path, model: str, out: Path, tz: str, root: Path) -> Run:
    r = Run(str(path.relative_to(root)).replace("\\", "/"), model)
    raw = path.read_bytes()
    try:
        cfg = get_settings()
        img = images.sanitize(raw, max_bytes=cfg.scanner_upload_max_bytes,
                              max_pixels=cfg.scanner_upload_max_pixels,
                              max_side=cfg.scanner_upload_max_side)
    except images.ImageRejected as e:
        r.error = f"image rejected: {e.code}"
        return r
    from app.scanner.extraction.service import _as_png
    r.captured = dates.captured_at(path.name, datetime.fromtimestamp(path.stat().st_mtime, UTC),
                                   tz)
    t0 = time.monotonic()
    try:
        res = vision.OpenAIVision(model).read(vision.prepare(_as_png(img.data)),
                                              captured=r.captured.isoformat(timespec="minutes"))
    except LLMError as e:
        r.error, r.seconds = e.reason + (f" ({e})" if e.reason != str(e) else ""), \
            time.monotonic() - t0
        return r
    r.seconds = time.monotonic() - t0
    r.input_tokens, r.output_tokens, r.parts = res.input_tokens, res.output_tokens, res.parts
    (out / _safe(model)).mkdir(parents=True, exist_ok=True)
    (out / _safe(model) / f"{path.stem}.json").write_text(
        json.dumps(res.data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        r.result = schema.parse(res.data)
    except schema.BadOutput:
        r.error = "bad_output"
        return r
    r.ok = True
    gold = root / "attendu" / f"{path.stem}.json"
    if gold.exists():
        r.score = score(r.result.entries,
                        json.loads(gold.read_text(encoding="utf-8")).get("entries", []))
    return r


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in name)


def _esc(s: str) -> str:
    return html.escape(s or "").replace("\n", "<br>")


_PRECISION = {"minute": "à la minute", "hour": "à l'heure", "day": "au jour",
              "week": "à la semaine", "month": "au mois", "year": "à l'année"}


def _entry_html(e: schema.Entry, captured: datetime | None, tz: str) -> str:
    when = dates.published(e.relative_time, captured, tz) if captured else None
    flags = [f for f, on in (("tronqué", e.text_truncated), ("coupé", e.partially_visible),
                             ("page", e.author_is_page)) if on]
    counts = [f"{label} {v}" for label, v in (("réactions", e.reactions_count),
                                               ("commentaires", e.comments_count),
                                               ("partages", e.shares_count)) if v is not None]
    meta = " · ".join(filter(None, [e.relative_time and (
        e.relative_time + (f" → {when[0].astimezone(dates.ZoneInfo(tz)):%d/%m %H:%M} "
                           f"({_PRECISION[when[1]]})" if when else " → ?")), *counts, *flags]))
    img = (f'<div class="it"><b>Texte dans l\'image :</b> <span dir="auto">{_esc(e.image_text)}'
           f"</span></div>" if e.image_text else "")
    return (f'<div class="e {e.kind}"><div class="h"><span class="k">{e.kind}</span> '
            f'<b dir="auto">{_esc(e.author) or "<i>auteur ?</i>"}</b> <small>{_esc(meta)}</small>'
            f'</div><div class="t" dir="auto">{_esc(e.text)}</div>{img}</div>')


def report(runs: list[Run], models: list[str], prices: dict[str, tuple[float, float]],
           out: Path, tz: str) -> Path:
    rows = []
    for m in models:
        rs = [r for r in runs if r.model == m]
        ok = [r for r in rs if r.ok]
        tin, tout = sum(r.input_tokens for r in rs), sum(r.output_tokens for r in rs)
        price = prices.get(m)
        cost = (tin * price[0] + tout * price[1]) / 1e6 if price else None
        scored = [r.score for r in ok if r.score]
        exp = sum(s["expected"] for s in scored)
        cers = [s["cer"] for s in scored if s["cer"] is not None]
        rows.append(
            f"<tr><td><b>{_esc(m)}</b></td><td>{len(ok)}/{len(rs)}</td>"
            f"<td>{sum(len(r.result.entries) for r in ok if r.result)}</td>"
            f"<td>{sum(r.seconds for r in ok) / max(1, len(ok)):.1f} s</td>"
            f"<td>{tin:,} / {tout:,}</td>"
            f"<td>{f'{cost:.3f} $ ({cost / max(1, len(ok)) * 1000:.2f} $ / 1000 captures)' if cost is not None else '— (--price)'}</td>"  # noqa: E501
            f"<td>{f'{sum(s["found"] for s in scored)}/{exp} trouvées, {sum(s["extra"] for s in scored)} en trop, auteurs exacts {sum(s["authors_exact"] for s in scored)}, textes exacts {sum(s["texts_exact"] for s in scored)}, erreur caractères {sum(cers) / len(cers):.1%}' if scored and cers else '—'}</td></tr>")  # noqa: E501
    cards = []
    for name in sorted({r.image for r in runs}):
        cols = []
        for m in models:
            r = next((x for x in runs if x.image == name and x.model == m), None)
            if r is None:
                continue
            if not r.ok:
                body = f'<p class="err">Échec : {_esc(r.error)}</p>'
            else:
                res = r.result
                assert res is not None
                ctx = " · ".join(filter(None, [res.platform, res.context.get("view"),
                                                res.context.get("page_or_group"),
                                                res.context.get("dialog_title")]))
                body = (f'<p class="meta">{_esc(ctx)} — confiance {res.confidence:.2f}'
                        f'{" — " + ", ".join(res.issues) if res.issues else ""} — '
                        f'{r.seconds:.1f} s, {r.input_tokens}+{r.output_tokens} jetons'
                        f'{f", {r.parts} parties" if r.parts > 1 else ""}'
                        f'{" — score " + _esc(json.dumps(r.score)) if r.score else ""}</p>'
                        + "".join(_entry_html(e, r.captured, tz) for e in res.entries))
            cols.append(f'<div class="col"><h3>{_esc(m)}</h3>{body}</div>')
        cards.append(f'<section><h2>{_esc(name)}</h2><div class="row">'
                     f'<a class="img" href="../{quote(name)}" target="_blank">'
                     f'<img src="../{quote(name)}" loading="lazy" alt=""></a>{"".join(cols)}'
                     f"</div></section>")
    page = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<title>Extraction Scanner — rapport</title><style>
body{{font:14px system-ui,sans-serif;margin:24px;color:#1d1d1f;background:#f6f6f4}}
table{{border-collapse:collapse;background:#fff;margin:12px 0 28px}}td,th{{border:1px solid #ddd;padding:6px 10px;text-align:start;vertical-align:top}}
section{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:16px 0}}
h2{{font-size:15px;margin:0 0 8px}}h3{{font-size:13px;margin:0 0 6px;color:#555}}
.row{{display:flex;gap:16px;align-items:flex-start}}.img{{flex:0 0 380px}}.img img{{width:380px;border:1px solid #ccc}}
.col{{flex:1;min-width:0}}.meta{{color:#666;font-size:12px;margin:0 0 8px}}.err{{color:#b00020}}
.e{{border-inline-start:3px solid #888;padding:4px 8px;margin:6px 0;background:#fafafa}}
.e.post{{border-color:#1b5fd1}}.e.comment{{border-color:#2e7d32}}.e.reply{{border-color:#c77700;margin-inline-start:22px}}
.k{{font-size:11px;text-transform:uppercase;color:#888}}.t{{margin-top:2px;white-space:normal}}.it{{margin-top:4px;color:#444;font-size:13px}}
small{{color:#777}}</style>
<h1>Extraction des captures — {len({r.image for r in runs})} images</h1>
<p>Généré le {datetime.now(UTC):%d/%m/%Y %H:%M} UTC · prompt {schema.PROMPT_VERSION}. Les textes sont copiés tels quels par le modèle ; Scanner ne fait aucune analyse.</p>
<table><tr><th>Modèle</th><th>Réussies</th><th>Entrées</th><th>Durée moy.</th><th>Jetons entrée / sortie</th><th>Coût estimé</th><th>Comparaison au texte attendu</th></tr>{"".join(rows)}</table>
{"".join(cards)}</html>"""  # noqa: E501
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--model", action="append", default=[],
                    help="vision model id (repeat to compare); default: the configured one")
    ap.add_argument("--limit", type=int, default=0, help="only the first N images")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--price", action="append", default=[],
                    help="MODEL=INPUT,OUTPUT in USD per million tokens, for the cost column")
    a = ap.parse_args(argv)
    cfg = get_settings()
    models = a.model or ([cfg.scanner_extraction_model] if cfg.scanner_extraction_model else [])
    if not models:
        print("Indiquez au moins un modèle : --model <id>", file=sys.stderr)
        return 2
    if not cfg.openai_api_key.get_secret_value():
        print("REPINTEL_OPENAI_API_KEY est vide (fichier .env).", file=sys.stderr)
        return 2
    prices = {}
    for p in a.price:
        try:
            m, v = p.split("=", 1)
            i, o = (float(x) for x in v.split(","))
            prices[m] = (i, o)
        except ValueError:
            print(f"--price invalide : {p}", file=sys.stderr)
            return 2
    if not a.folder.is_dir():
        print(f"Dossier introuvable : {a.folder}", file=sys.stderr)
        return 2
    skip = {"rapport-extraction", "attendu"}
    files = sorted((p for p in a.folder.rglob("*")
                    if p.is_file() and p.suffix.lower() in EXTS
                    and not skip & set(p.relative_to(a.folder).parts[:-1])),
                   key=lambda p: str(p.relative_to(a.folder)).lower())
    if a.limit:
        files = files[: a.limit]
    if not files:
        seen = sorted(p.name for p in a.folder.iterdir())[:10]
        print(f"Aucune image (.png, .jpg, .jpeg, .webp) dans {a.folder} ni ses sous-dossiers. "
              f"Contenu vu : {seen or 'dossier vide'}", file=sys.stderr)
        return 2
    out = a.folder / "rapport-extraction"
    out.mkdir(exist_ok=True)
    tz = cfg.scanner_extraction_timezone
    jobs = [(f, m) for m in models for f in files]
    print(f"{len(files)} images × {len(models)} modèle(s) …", flush=True)
    runs: list[Run] = []
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        for n, r in enumerate(pool.map(lambda j: run_one(j[0], j[1], out, tz, a.folder), jobs), 1):
            runs.append(r)
            state = f"{len(r.result.entries)} entrées" if r.ok and r.result else r.error
            print(f"[{n}/{len(jobs)}] {r.model} · {r.image} · {state}", flush=True)
            if not r.ok and r.error.startswith(("auth_failed", "quota_exceeded",
                                                "model_not_found", "not_configured")):
                print("Arrêt : " + r.error, file=sys.stderr)
                break
    path = report(runs, models, prices, out, tz)
    print(f"Rapport : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
