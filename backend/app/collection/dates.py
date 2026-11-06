"""Publication dates. Returns (datetime, how) or (None, None); never guesses silently.
`how` is stored with the mention so approximate dates are labelled as such."""

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

_UNITS = {
    "minute": 1, "minutes": 1, "min": 1, "mins": 1, "minuto": 1,
    "hour": 60, "hours": 60, "heure": 60, "heures": 60, "h": 60,
    "day": 1440, "days": 1440, "jour": 1440, "jours": 1440,
    "week": 10080, "weeks": 10080, "semaine": 10080, "semaines": 10080,
    "دقيقة": 1, "دقائق": 1, "ساعة": 60, "ساعات": 60, "يوم": 1440, "أيام": 1440, "ايام": 1440,
    "أسبوع": 10080, "اسبوع": 10080, "أسابيع": 10080,
}
_REL = re.compile(r"(\d+)\s*([^\d\s]+)")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse_absolute(s: str | None) -> datetime | None:
    if not s or not s.strip():
        return None
    s = s.strip()
    try:
        return _aware(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return _aware(parsedate_to_datetime(s))   # RFC 822 (RSS)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%b %d, %Y", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_relative(s: str | None, now: datetime | None = None) -> datetime | None:
    """'3 hours ago' / 'il y a 3 heures' / 'قبل 3 ساعات' → approximate datetime."""
    if not s:
        return None
    m = _REL.search(s.translate(_AR_DIGITS).lower())
    if not m or m.group(2).rstrip(".") not in _UNITS:
        return None
    minutes = int(m.group(1)) * _UNITS[m.group(2).rstrip(".")]
    return (now or datetime.now(UTC)) - timedelta(minutes=minutes)


def parse_provider_date(s: str | None, now: datetime | None = None
                        ) -> tuple[datetime | None, str | None]:
    if d := parse_absolute(s):
        return d, "provider"
    if d := parse_relative(s, now):
        return d, "provider_relative"   # approximate (e.g. "3 hours ago")
    return None, None
