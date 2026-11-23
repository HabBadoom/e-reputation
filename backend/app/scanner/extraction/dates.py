"""When a screenshot was taken, and the publication time shown in it ("9 h", "1 j", "Hier").

Facebook shows relative times. They are converted to an estimated timestamp from the capture
time, with the precision they allow. Unknown formats give None: the raw text is always kept.
"""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Capture time from common screenshot file names (local time of the device).
_D = r"(?P<y>20\d\d)-(?P<m>\d\d)-(?P<d>\d\d)"
_FILENAME_PATTERNS = [
    # Windows: "Capture d'écran 2026-11-23 064458.png", "Screenshot 2026-11-23 064458"
    re.compile(_D + r"[ _](?P<H>\d\d)(?P<M>\d\d)(?P<S>\d\d)\b"),
    # WhatsApp "… 2026-11-23 at 08.21.26"; iOS/macOS "… 2026-11-23 à 08.14.26"
    re.compile(_D + r" (?:à|at) (?P<H>\d\d)[.:](?P<M>\d\d)[.:](?P<S>\d\d)"),
    # Android: "Screenshot_20261123-081426", "Screenshot_2026-11-23-08-14-26"
    re.compile(r"(?P<y>20\d\d)-?(?P<m>\d\d)-?(?P<d>\d\d)[-_]"
               r"(?P<H>\d\d)-?(?P<M>\d\d)-?(?P<S>\d\d)"),
]
# Scanner's own browser captures: "capture-host-20261123-081426.png" (UTC)
_SCANNER_CAPTURE = re.compile(r"^capture-.*-(?P<y>20\d\d)(?P<m>\d\d)(?P<d>\d\d)"
                              r"-(?P<H>\d\d)(?P<M>\d\d)(?P<S>\d\d)\.png$")


def captured_at(filename: str, fallback: datetime, tz: str) -> datetime:
    """Capture time from the file name when plausible (not in the future, not older than
    the fallback by more than a year), else the fallback (upload time)."""
    zone = ZoneInfo(tz)
    m = _SCANNER_CAPTURE.match(filename or "")
    candidates = [(m, UTC)] if m else [(p.search(filename or ""), zone) for p in _FILENAME_PATTERNS]
    for match, z in candidates:
        if not match:
            continue
        g = {k: int(v) for k, v in match.groupdict().items()}
        try:
            dt = datetime(g["y"], g["m"], g["d"], g["H"], g["M"], g["S"], tzinfo=z)
        except ValueError:
            continue
        if fallback - timedelta(days=366) <= dt <= fallback + timedelta(minutes=5):
            return dt.astimezone(UTC)
    return fallback


_UNITS = {
    "minute": ("min", "mn", "m", "mins", "minute", "minutes", "د", "دقيقة", "دقائق", "دقيقتين"),
    "hour": ("h", "hr", "hrs", "heure", "heures", "hour", "hours", "س", "ساعة", "ساعات", "ساعتين"),
    "day": ("j", "d", "jour", "jours", "day", "days", "ي", "يوم", "أيام", "ايام", "يومين"),
    "week": ("sem", "sem.", "w", "wk", "semaine", "semaines", "week", "weeks", "أ", "أسبوع",
             "اسبوع", "أسابيع", "اسابيع", "أسبوعين"),
    "month": ("mois", "mo", "month", "months", "شهر", "أشهر", "اشهر", "شهور", "شهرين"),
    "year": ("a", "an", "ans", "y", "yr", "year", "years", "سنة", "سنوات", "عام", "أعوام", "سنتين"),
}
_UNIT_OF = {alias: unit for unit, aliases in _UNITS.items() for alias in aliases}
_DELTA = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1),
          "week": timedelta(weeks=1), "month": timedelta(days=30), "year": timedelta(days=365)}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_REL = re.compile(r"^(?:il y a|منذ|ago)?\s*(\d+)\s*([^\d\s]+\.?)\s*(?:ago)?$")
_DUAL = {"دقيقتين": "minute", "ساعتين": "hour", "يومين": "day", "أسبوعين": "week",
         "شهرين": "month", "سنتين": "year"}
_NOW = ("à l'instant", "a l'instant", "just now", "now", "maintenant", "الآن", "الان", "للتو")
_YESTERDAY = ("hier", "yesterday", "أمس", "امس", "البارحة")


_MONTHS = {
    "janv": 1, "janvier": 1, "jan": 1, "january": 1, "févr": 2, "fevr": 2, "février": 2,
    "fevrier": 2, "feb": 2, "february": 2, "mars": 3, "mar": 3, "march": 3, "avr": 4,
    "avril": 4, "apr": 4, "april": 4, "mai": 5, "may": 5, "juin": 6, "jun": 6, "june": 6,
    "juil": 7, "juillet": 7, "jul": 7, "july": 7, "août": 8, "aout": 8, "aug": 8, "august": 8,
    "sept": 9, "septembre": 9, "sep": 9, "september": 9, "oct": 10, "octobre": 10,
    "october": 10, "nov": 11, "novembre": 11, "november": 11, "déc": 12, "dec": 12,
    "décembre": 12, "decembre": 12, "december": 12,
}
_CLOCK = re.compile(r"(?:à|a|at)\s+(\d{1,2})[:h](\d\d)")
_DAY_MONTH = re.compile(r"^(\d{1,2})\s+([a-zéû]+)\.?(?:\s+(20\d\d))?")
_MONTH_DAY = re.compile(r"^([a-z]+)\.?\s+(\d{1,2})(?:,?\s+(20\d\d))?")


def _absolute(raw: str, captured: datetime, zone: ZoneInfo) -> tuple[datetime, str] | None:
    m = _DAY_MONTH.match(raw)
    day = month = year = None
    if m and m.group(2) in _MONTHS:
        day, month, year = int(m.group(1)), _MONTHS[m.group(2)], m.group(3)
    else:
        m = _MONTH_DAY.match(raw)
        if m and m.group(1) in _MONTHS:
            day, month, year = int(m.group(2)), _MONTHS[m.group(1)], m.group(3)
    if day is None or month is None:
        return None
    local = captured.astimezone(zone)
    y = int(year) if year else local.year
    clock = _CLOCK.search(raw)
    h, mi = (int(clock.group(1)), int(clock.group(2))) if clock else (12, 0)
    try:
        dt = datetime(y, month, day, h, mi, tzinfo=zone)
    except ValueError:
        return None
    if not year and dt > local + timedelta(days=1):     # "12 déc." seen in January
        dt = dt.replace(year=y - 1)
    return dt.astimezone(UTC), "minute" if clock else "day"


def published(relative: str, captured: datetime, tz: str = "Africa/Algiers"
              ) -> tuple[datetime, str] | None:
    """Estimated publication time and its precision, or None if the format is unknown.
    Clock times and dates without a zone are read in `tz` (the agents' local time)."""
    raw = " ".join((relative or "").translate(_DIGITS).lower().replace("·", " ").split())
    if not raw:
        return None
    zone = ZoneInfo(tz)
    if raw in _NOW:
        return captured, "minute"
    if raw.split(" ")[0] in _YESTERDAY:
        clock = _CLOCK.search(raw)
        day = (captured.astimezone(zone) - timedelta(days=1)).date()
        if clock:
            try:
                return datetime(day.year, day.month, day.day, int(clock.group(1)),
                                int(clock.group(2)), tzinfo=zone).astimezone(UTC), "minute"
            except ValueError:
                pass
        return captured - timedelta(days=1), "day"
    found = _absolute(raw, captured, zone)
    if found:
        return found
    for word, unit in _DUAL.items():
        if word in raw:
            return captured - 2 * _DELTA[unit], unit
    m = _REL.match(raw)
    if m:
        unit = _UNIT_OF.get(m.group(2).rstrip("."))
        if unit:
            return captured - int(m.group(1)) * _DELTA[unit], unit
    return None
