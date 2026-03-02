import json
import os
from datetime import datetime
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "narrative_synthesis.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def extract_episode(segment_dialogue: str, current_date: str = None) -> dict:
    """
    Given raw dialogue from a segment, produce Episode + Atomic Facts + Foresight + Scene Hint
    in a single LLM call.

    Returns:
        {"episode": str, "atomic_facts": list[str], "foresight": list[dict], "scene_hint": dict}
    """
    if current_date is None:
        current_date = datetime.now().strftime("%Y-%m-%d")

    prompt = _load_prompt().replace("{segment}", segment_dialogue).replace("{current_date}", current_date)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    parsed = json.loads(text)
    return parsed
