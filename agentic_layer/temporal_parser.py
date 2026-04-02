import re
import json
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

# Fast pre-filter: skip LLM if no date-like words
_DATE_PATTERNS = re.compile(
    r'\b('
    r'january|february|march|april|may|june|july|august|september|october|november|december'
    r'|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec'
    r'|monday|tuesday|wednesday|thursday|friday|saturday|sunday'
    r'|yesterday|today|tomorrow|last\s+week|last\s+month|last\s+year'
    r'|this\s+week|this\s+month|this\s+year|next\s+week|next\s+month'
    r'|ago|before|after|during|since|until|recent|latest'
    r'|\d{4}|\d{1,2}/\d{1,2}'
    r'|when\s+did|when\s+was|what\s+date|what\s+day|how\s+long\s+ago'
    r')\b',
    re.IGNORECASE
)


def _has_temporal_signal(query: str) -> bool:
    return bool(_DATE_PATTERNS.search(query))


def parse_temporal_query(query: str, reference_date: str = None) -> dict | None:
    """Parse temporal references from a query using LLM.

    Returns: {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"} or None if no date reference.
    """
    if not _has_temporal_signal(query):
        return None

    ref_context = f"\nCurrent/reference date: {reference_date}" if reference_date else ""

    prompt = f"""Does this query contain a date or time reference? If yes, extract the date range.
{ref_context}

Query: {query}

Rules:
- "in July 2023" → {{"date_from": "2023-07-01", "date_to": "2023-07-31"}}
- "last week" (from 2023-10-22) → {{"date_from": "2023-10-15", "date_to": "2023-10-22"}}
- "in 2022" → {{"date_from": "2022-01-01", "date_to": "2022-12-31"}}
- "before June" → {{"date_from": "2023-01-01", "date_to": "2023-05-31"}}
- No date reference → {{"has_date": false}}

Return ONLY JSON, no extra text."""

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        if response.text is None:
            return None
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        result = json.loads(text)
        if result.get("has_date") is False:
            return None
        if "date_from" in result and "date_to" in result:
            return {"date_from": result["date_from"], "date_to": result["date_to"]}
        return None
    except Exception:
        return None
