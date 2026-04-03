import re
from datetime import datetime, timedelta


def parse_temporal_query(query: str, reference_date: str = None) -> dict | None:
    """Parse temporal references from a query using regex only. No LLM call.

    Returns: {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"} or None.
    """
    ref = _parse_reference_date(reference_date)
    q = query.lower().strip()

    # Try each pattern in order of specificity
    for parser in [
        _parse_explicit_date,       # "29th march", "march 29", "29/03/2026"
        _parse_relative_day,        # "kal", "parso", "yesterday", "today"
        _parse_relative_week,       # "last week", "this week", "pichle hafte"
        _parse_relative_month,      # "last month", "is mahine", "pichle mahine"
        _parse_month_name,          # "in march", "march me"
        _parse_n_ago,               # "3 days ago", "2 weeks ago", "1 month ago"
        _parse_year,                # "in 2025", "2025 me"
    ]:
        result = parser(q, ref)
        if result:
            return result

    return None


def _parse_reference_date(reference_date: str | None) -> datetime:
    """Parse reference date string or use today."""
    if reference_date:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"]:
            try:
                return datetime.strptime(reference_date.split(".")[0].split("+")[0].strip(), fmt)
            except ValueError:
                continue
    return datetime.now()


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _last_day(year: int, month: int) -> int:
    """Last day of a given month."""
    if month == 12:
        return 31
    return (datetime(year, month + 1, 1) - timedelta(days=1)).day


def _parse_explicit_date(q: str, ref: datetime) -> dict | None:
    """Match: '29th march', '29 march 2026', 'march 29', '29/03/2026', '29-03-2026'."""

    # "29th march 2026" / "29 march" / "29th march"
    m = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?', q)
    if m:
        day = int(m.group(1))
        month = _MONTHS[m.group(2)]
        year = int(m.group(3)) if m.group(3) else ref.year
        dt = datetime(year, month, day)
        return {"date_from": _fmt(dt), "date_to": _fmt(dt)}

    # "march 29 2026" / "march 29th" / "march 29"
    m = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?', q)
    if m:
        month = _MONTHS[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else ref.year
        dt = datetime(year, month, day)
        return {"date_from": _fmt(dt), "date_to": _fmt(dt)}

    # "29/03/2026" or "29-03-2026"
    m = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', q)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dt = datetime(year, month, day)
        return {"date_from": _fmt(dt), "date_to": _fmt(dt)}

    # "2026-03-29" (ISO format)
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', q)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dt = datetime(year, month, day)
        return {"date_from": _fmt(dt), "date_to": _fmt(dt)}

    return None


def _parse_relative_day(q: str, ref: datetime) -> dict | None:
    """Match: 'yesterday', 'today', 'day before yesterday', 'kal', 'parso', 'aaj'."""

    patterns = {
        r'\b(today|aaj)\b': 0,
        r'\b(yesterday|kal|beeta\s*kal)\b': -1,
        r'\b(day\s+before\s+yesterday|parso|parson)\b': -2,
        r'\b(tomorrow|kal\s+ko|aane\s*wala\s*kal)\b': 1,
        r'\b(day\s+after\s+tomorrow)\b': 2,
    }

    for pattern, delta in patterns.items():
        if re.search(pattern, q):
            dt = ref + timedelta(days=delta)
            return {"date_from": _fmt(dt), "date_to": _fmt(dt)}

    return None


def _parse_relative_week(q: str, ref: datetime) -> dict | None:
    """Match: 'last week', 'this week', 'pichle hafte', 'is hafte'."""

    if re.search(r'\b(last\s+week|pichle\s+hafte|pichhle\s+hafte|pichle\s+week)\b', q):
        # Monday to Sunday of last week
        monday = ref - timedelta(days=ref.weekday() + 7)
        sunday = monday + timedelta(days=6)
        return {"date_from": _fmt(monday), "date_to": _fmt(sunday)}

    if re.search(r'\b(this\s+week|is\s+hafte|is\s+week)\b', q):
        monday = ref - timedelta(days=ref.weekday())
        return {"date_from": _fmt(monday), "date_to": _fmt(ref)}

    return None


def _parse_relative_month(q: str, ref: datetime) -> dict | None:
    """Match: 'last month', 'this month', 'pichle mahine', 'is mahine'."""

    if re.search(r'\b(last\s+month|pichle\s+mahine|pichhle\s+mahine|pichle\s+month)\b', q):
        first = (ref.replace(day=1) - timedelta(days=1)).replace(day=1)
        last = ref.replace(day=1) - timedelta(days=1)
        return {"date_from": _fmt(first), "date_to": _fmt(last)}

    if re.search(r'\b(this\s+month|is\s+mahine|is\s+month)\b', q):
        first = ref.replace(day=1)
        return {"date_from": _fmt(first), "date_to": _fmt(ref)}

    return None


def _parse_month_name(q: str, ref: datetime) -> dict | None:
    """Match: 'in march', 'march me', 'march mein'."""

    m = re.search(r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b', q)
    if not m:
        return None

    # Only match if it's a standalone month reference, not part of an explicit date (already caught above)
    # Check no digit immediately before or after
    start, end = m.span()
    before = q[max(0, start-3):start]
    after = q[end:min(len(q), end+3)]
    if re.search(r'\d', before) or re.search(r'^\s*\d', after):
        return None

    month = _MONTHS[m.group(1)]
    year = ref.year
    # If month is in the future for this year, assume last year
    if month > ref.month:
        year -= 1
    first = datetime(year, month, 1)
    last_day = _last_day(year, month)
    last = datetime(year, month, last_day)
    return {"date_from": _fmt(first), "date_to": _fmt(last)}


def _parse_n_ago(q: str, ref: datetime) -> dict | None:
    """Match: '3 days ago', '2 weeks ago', '1 month ago', '3 din pehle', '2 hafte pehle'."""

    # English: "N days/weeks/months ago"
    m = re.search(r'(\d+)\s+(days?|weeks?|months?|years?)\s+ago', q)
    if not m:
        # Hinglish: "N din/hafte/mahine pehle"
        m = re.search(r'(\d+)\s+(din|hafte|hafta|mahine|mahina|saal)\s+(pehle|pahle|pehele)', q)

    if not m:
        return None

    n = int(m.group(1))
    unit = m.group(2).lower()

    if unit in ("day", "days", "din"):
        dt = ref - timedelta(days=n)
        return {"date_from": _fmt(dt), "date_to": _fmt(dt)}
    elif unit in ("week", "weeks", "hafte", "hafta"):
        start = ref - timedelta(weeks=n)
        return {"date_from": _fmt(start), "date_to": _fmt(ref)}
    elif unit in ("month", "months", "mahine", "mahina"):
        month = ref.month - n
        year = ref.year
        while month <= 0:
            month += 12
            year -= 1
        first = datetime(year, month, 1)
        last_day = _last_day(year, month)
        last = datetime(year, month, last_day)
        return {"date_from": _fmt(first), "date_to": _fmt(last)}
    elif unit in ("year", "years", "saal"):
        year = ref.year - n
        return {"date_from": f"{year}-01-01", "date_to": f"{year}-12-31"}

    return None


def _parse_year(q: str, ref: datetime) -> dict | None:
    """Match: 'in 2025', '2025 me', '2025 mein'."""

    m = re.search(r'\b(20\d{2})\b', q)
    if m:
        # Make sure it's not part of a full date (already caught)
        year = int(m.group(1))
        # Check it's a standalone year reference
        start, end = m.span()
        before = q[max(0, start-2):start]
        after = q[end:min(len(q), end+2)]
        if re.search(r'[-/]', before) or re.search(r'^[-/]', after):
            return None
        return {"date_from": f"{year}-01-01", "date_to": f"{year}-12-31"}

    return None
