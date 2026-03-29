import re
import json
from datetime import datetime, timedelta
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

# --- Rule-based patterns (resolved without LLM, ~0ms) ---

# Past tense markers in Hinglish
_PAST_TENSE = re.compile(r'\b(tha|thi|the|kiya|kari|kri|gaya|gayi|gaye|khaya|dekha|liya|suna|piya|mila)\b', re.IGNORECASE)
# Future tense markers
_FUTURE_TENSE = re.compile(r'\b(ga|gi|ge|na\s+hai|karenge|karunga|karungi|jayega|jayegi|hoga|hogi)\b', re.IGNORECASE)

# Pattern: X din/days pehle/pahle/ago
_X_DAYS_AGO = re.compile(r'(\d+)\s*(?:din|days?)\s*(?:pehle|pahle|ago)', re.IGNORECASE)
# Pattern: X hafte/weeks pehle/ago
_X_WEEKS_AGO = re.compile(r'(\d+)\s*(?:hafte|hafton|weeks?)\s*(?:pehle|pahle|ago)', re.IGNORECASE)
# Pattern: X mahine/months pehle/ago
_X_MONTHS_AGO = re.compile(r'(\d+)\s*(?:mahine|mahinon|months?)\s*(?:pehle|pahle|ago)', re.IGNORECASE)

# Fixed relative dates
_KAL = re.compile(r'\bkal\b', re.IGNORECASE)
_PARSO = re.compile(r'\bparso\b', re.IGNORECASE)
_AAJ = re.compile(r'\baaj\b|\btoday\b', re.IGNORECASE)
_YESTERDAY = re.compile(r'\byesterday\b', re.IGNORECASE)
_TOMORROW = re.compile(r'\btomorrow\b', re.IGNORECASE)

# Range patterns
_PICHLE_HAFTE = re.compile(r'\b(?:pichle|pichla|last)\s*(?:hafte|hafta|week)\b', re.IGNORECASE)
_IS_HAFTE = re.compile(r'\b(?:is|iss)\s*(?:hafte|hafta)\b|\bthis\s*week\b', re.IGNORECASE)
_PICHLE_MAHINE = re.compile(r'\b(?:pichle|pichla|last)\s*(?:mahine|mahina|month)\b', re.IGNORECASE)
_IS_MAHINE = re.compile(r'\b(?:is|iss)\s*(?:mahine|mahina)\b|\bthis\s*month\b', re.IGNORECASE)

# Explicit date patterns
_EXPLICIT_DATE_YMD = re.compile(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})')
_EXPLICIT_DATE_DMY = re.compile(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})')

# Month names → number
_MONTH_MAP = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
    'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6,
    'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
}
_MONTH_NAME = re.compile(
    r'\b(?:in\s+)?(' + '|'.join(_MONTH_MAP.keys()) + r')\b',
    re.IGNORECASE
)

# Mixed query detection (past + present/future markers)
_MIXED_MARKERS = re.compile(r'\b(aur|and|bhi|also)\b', re.IGNORECASE)

# Keywords that indicate the query has no real temporal intent despite having temporal words
_FALSE_POSITIVE_PATTERNS = re.compile(
    r'\b(pasand|favourite|favorite|best|prefer|like|naam|name|kaun|kaunsa|kaunsi)\b',
    re.IGNORECASE
)


def _fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def _is_past(query: str) -> bool:
    return bool(_PAST_TENSE.search(query))


def _is_future(query: str) -> bool:
    return bool(_FUTURE_TENSE.search(query))


def _rule_based_parse(query: str, today: datetime) -> dict | None:
    """
    Try to resolve temporal expressions using regex rules.
    Returns result dict or None if no pattern matches.
    """
    # Skip queries that have temporal keywords but no real temporal intent
    # e.g., "kal kaunsi movie dekhi" is temporal, but "kaunsa khana pasand hai" is not
    # This only applies when the temporal keyword is "aaj" and query is about preferences

    # Explicit dates: YYYY-MM-DD or DD/MM/YYYY
    m = _EXPLICIT_DATE_YMD.search(query)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                    "reasoning": f"Explicit date: {_fmt(d)}"}
        except ValueError:
            pass

    m = _EXPLICIT_DATE_DMY.search(query)
    if m:
        try:
            d = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                    "reasoning": f"Explicit date: {_fmt(d)}"}
        except ValueError:
            pass

    # X din pehle
    m = _X_DAYS_AGO.search(query)
    if m:
        days = int(m.group(1))
        d = today - timedelta(days=days)
        return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                "reasoning": f"{days} days ago = {_fmt(d)}"}

    # X hafte pehle
    m = _X_WEEKS_AGO.search(query)
    if m:
        weeks = int(m.group(1))
        d_from = today - timedelta(weeks=weeks)
        d_to = d_from + timedelta(days=6)
        return {"date_from": _fmt(d_from), "date_to": _fmt(d_to), "is_mixed": False,
                "reasoning": f"{weeks} weeks ago"}

    # X mahine pehle
    m = _X_MONTHS_AGO.search(query)
    if m:
        months = int(m.group(1))
        month = today.month - months
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        d_from = datetime(year, month, 1)
        # Last day of that month
        if month == 12:
            d_to = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            d_to = datetime(year, month + 1, 1) - timedelta(days=1)
        return {"date_from": _fmt(d_from), "date_to": _fmt(d_to), "is_mixed": False,
                "reasoning": f"{months} months ago = {_fmt(d_from)} to {_fmt(d_to)}"}

    # pichle hafte / last week
    if _PICHLE_HAFTE.search(query):
        # Last 7 days from yesterday
        d_to = today - timedelta(days=1)
        d_from = today - timedelta(days=7)
        return {"date_from": _fmt(d_from), "date_to": _fmt(d_to), "is_mixed": False,
                "reasoning": "Last week"}

    # is hafte / this week
    if _IS_HAFTE.search(query):
        # Monday of this week to today
        d_from = today - timedelta(days=today.weekday())
        return {"date_from": _fmt(d_from), "date_to": _fmt(today), "is_mixed": False,
                "reasoning": "This week"}

    # pichle mahine / last month
    if _PICHLE_MAHINE.search(query):
        month = today.month - 1
        year = today.year
        if month <= 0:
            month += 12
            year -= 1
        d_from = datetime(year, month, 1)
        if month == 12:
            d_to = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            d_to = datetime(year, month + 1, 1) - timedelta(days=1)
        return {"date_from": _fmt(d_from), "date_to": _fmt(d_to), "is_mixed": False,
                "reasoning": "Last month"}

    # is mahine / this month
    if _IS_MAHINE.search(query):
        d_from = datetime(today.year, today.month, 1)
        return {"date_from": _fmt(d_from), "date_to": _fmt(today), "is_mixed": False,
                "reasoning": "This month"}

    # Month name (e.g., "in March", "march me", "October 2023")
    m = _MONTH_NAME.search(query)
    if m:
        month_num = _MONTH_MAP[m.group(1).lower()]

        # Check for explicit 4-digit year alongside the month name
        year_match = re.search(r'\b(20\d{2})\b', query)
        if year_match:
            year = int(year_match.group(1))
            d_from = datetime(year, month_num, 1)
            if month_num == 12:
                d_to = datetime(year + 1, 1, 1) - timedelta(days=1)
            else:
                d_to = datetime(year, month_num + 1, 1) - timedelta(days=1)
            today_naive = today.replace(tzinfo=None) if today.tzinfo else today
            d_to_naive = d_to.replace(tzinfo=None) if d_to.tzinfo else d_to
            if d_to_naive > today_naive:
                d_to = today
            return {"date_from": _fmt(d_from), "date_to": _fmt(d_to), "is_mixed": False,
                    "reasoning": f"Month: {m.group(1)} Year: {year}"}

        # No explicit year — fall through to LLM.
        # Rule-based year inference (today.year ± 1) is unreliable when the
        # conversation happened years in the past. The LLM, given the correct
        # reference date (current_time_ist = last session date), resolves correctly.
        return None

    # "yesterday"
    if _YESTERDAY.search(query):
        d = today - timedelta(days=1)
        return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                "reasoning": "Yesterday"}

    # "tomorrow"
    if _TOMORROW.search(query):
        d = today + timedelta(days=1)
        return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                "reasoning": "Tomorrow"}

    # "kal" — yesterday if past tense, tomorrow if future tense
    if _KAL.search(query):
        # Check for mixed: "kal kya khaya aur aaj kya karna hai"
        has_mixed = bool(_MIXED_MARKERS.search(query)) and bool(_AAJ.search(query))
        if has_mixed:
            if _is_past(query):
                d = today - timedelta(days=1)
                return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": True,
                        "reasoning": "kal (past) + aaj = mixed"}
            else:
                d = today + timedelta(days=1)
                return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": True,
                        "reasoning": "kal (future) + aaj = mixed"}

        if _is_past(query):
            d = today - timedelta(days=1)
            return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                    "reasoning": "kal = yesterday (past tense)"}
        elif _is_future(query):
            d = today + timedelta(days=1)
            return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                    "reasoning": "kal = tomorrow (future tense)"}
        else:
            # Default to yesterday for ambiguous "kal"
            d = today - timedelta(days=1)
            return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                    "reasoning": "kal = yesterday (default)"}

    # "parso" — 2 days ago if past, 2 days ahead if future
    if _PARSO.search(query):
        if _is_past(query):
            d = today - timedelta(days=2)
        elif _is_future(query):
            d = today + timedelta(days=2)
        else:
            d = today - timedelta(days=2)  # default to past
        return {"date_from": _fmt(d), "date_to": _fmt(d), "is_mixed": False,
                "reasoning": f"parso = {_fmt(d)}"}

    # "aaj" / "today" — only if query has temporal intent (not just preference queries)
    if _AAJ.search(query):
        # Skip if query is about preferences, not events
        if _FALSE_POSITIVE_PATTERNS.search(query):
            return None
        return {"date_from": _fmt(today), "date_to": _fmt(today), "is_mixed": False,
                "reasoning": "Today"}

    return None


# --- LLM fallback prompt (only for complex cases rules can't handle) ---

_TEMPORAL_PROMPT = """You are a temporal expression resolver for a Hinglish (Hindi + English) chatbot.

Reference date/time (IST): {current_time_ist}
This is the date to treat as "today" — it may be a past date if the conversation being queried is historical.

User query: "{query}"

Analyze the query and extract any temporal references. Resolve them to specific date ranges relative to the reference date above.

Rules:
- "kal" means "yesterday" if past tense (tha/thi/the), "tomorrow" if future tense (ga/gi/ge/na hai)
- "parso" means 2 days ago (past) or 2 days from now (future), based on tense
- "aaj" = the reference date
- "X din pehle" = X days before the reference date
- "pichle hafte" = last 7 days before reference date
- "is hafte" = this week (Monday to reference date)
- "pichle mahine" = last calendar month before reference date
- Month name only (e.g., "in July", "camping in July"): resolve to that month in the most likely year relative to the reference date. If the month has already passed in the reference year, use that year. If it is still upcoming in the reference year, use the prior year.
- Month + explicit year (e.g., "October 2023"): use that exact year and full calendar month.
- All dates should be in YYYY-MM-DD format
- If the query has no temporal expression, return null
- If the query asks about BOTH past and present/future, set is_mixed: true

Output JSON only:
{
  "date_from": "YYYY-MM-DD",
  "date_to": "YYYY-MM-DD",
  "is_mixed": false,
  "reasoning": "brief explanation"
}
or null if no temporal expression found.

Return ONLY the JSON or the word null, nothing else."""


# Regex pre-filter for LLM fallback — only check if any temporal keyword exists
_TEMPORAL_KEYWORDS = re.compile(
    r'\b(kal|parso|aaj|today|yesterday|tomorrow|pehle|pahle|pichle|pichla|'
    r'last\s+week|last\s+month|is\s+hafte|is\s+mahine|ago|din\s+pehle|'
    r'hafte|mahine|saal|'
    r'\d+\s*(?:din|days?|weeks?|months?|years?)\s*(?:pehle|pahle|ago)|'
    r'january|february|march|april|may|june|'
    r'july|august|september|october|november|december|'
    r'jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|'
    r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b',
    re.IGNORECASE
)


def parse_temporal_query(query: str, current_time_ist: datetime) -> dict | None:
    """
    Detect and resolve temporal expressions in the query.

    Strategy:
    1. Try rule-based regex parsing first (instant, ~0ms)
    2. Fall back to LLM only for complex cases rules can't handle (~0.5-3s)

    Args:
        query: User's query text (Hinglish or English)
        current_time_ist: Current time in IST (for resolving relative expressions)

    Returns:
        {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD", "is_mixed": bool} or None
    """
    # Quick exit: no temporal keywords at all
    if not _TEMPORAL_KEYWORDS.search(query):
        return None

    # Try rule-based parsing first (instant)
    today = current_time_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    result = _rule_based_parse(query, today)
    if result is not None:
        print(f"  [temporal] Rule-based: {result['reasoning']}")
        return result

    # Only fall back to LLM if there's a strong temporal signal (not just a month name in casual chat)
    # Month names alone in conversational context ("course june me khtm hoga") shouldn't trigger LLM
    _STRONG_TEMPORAL = re.compile(
        r'\b(kal|parso|pehle|pahle|pichle|pichla|ago|din\s+pehle|yesterday|tomorrow|last\s+week|last\s+month)\b',
        re.IGNORECASE
    )
    if not _STRONG_TEMPORAL.search(query):
        return None

    print(f"  [temporal] Falling back to LLM...")
    prompt = _TEMPORAL_PROMPT.replace(
        "{current_time_ist}", current_time_ist.strftime("%Y-%m-%d %H:%M")
    ).replace("{query}", query)

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip()

        if text.lower() in ("null", "none"):
            return None

        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]

        parsed = json.loads(text)

        if not parsed or "date_from" not in parsed or "date_to" not in parsed:
            return None
        return parsed
    except Exception as e:
        print(f"  [temporal] LLM parse failed: {e}")
        return None
