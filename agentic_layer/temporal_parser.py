import json
from datetime import datetime
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

_TEMPORAL_PROMPT = """You are a temporal expression resolver for a Hinglish (Hindi + English) chatbot.

Current date/time (IST): {current_time_ist}

User query: "{query}"

Analyze the query and extract any temporal references. Resolve them to specific date ranges.

Rules:
- "kal" means "yesterday" if past tense (tha/thi/the), "tomorrow" if future tense (ga/gi/ge/na hai)
- "parso" means 2 days ago (past) or 2 days from now (future), based on tense
- "aaj" = today
- "X din pehle" = X days ago
- "pichle hafte" = last 7 days
- "is hafte" = this week (Monday to today)
- "pichle mahine" = last month
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


def parse_temporal_query(query: str, current_time_ist: datetime) -> dict | None:
    """
    Detect and resolve temporal expressions in the query using LLM.

    Args:
        query: User's query text (Hinglish or English)
        current_time_ist: Current time in IST (for resolving relative expressions)

    Returns:
        {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD", "is_mixed": bool} or None
    """
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
        print(f"  [temporal] Parse failed: {e}")
        return None
