import json
import os
from datetime import datetime
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "foresight_extraction.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def extract_foresight(episode_text: str, current_date: str = None) -> list[dict]:
    """
    Extract time-bounded foresight signals from an episode.

    Args:
        episode_text: The third-person episode narrative.
        current_date: Date string for temporal context.

    Returns:
        List of {"description": str, "valid_from": str|None, "valid_until": str|None}
    """
    if current_date is None:
        current_date = datetime.now().strftime("%Y-%m-%d")

    prompt = _load_prompt().replace("{episode}", episode_text).replace("{current_date}", current_date)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    parsed = json.loads(text)
    return parsed.get("foresight", [])
