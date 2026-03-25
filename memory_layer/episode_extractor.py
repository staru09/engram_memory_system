import json
import os
from datetime import datetime, timezone, timedelta
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "narrative_synthesis.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def extract_episode(segment_dialogue: str, current_date: str = None,
                    prior_facts: list[str] = None) -> dict:
    """
    Given raw dialogue from a segment, produce Episode + Atomic Facts + Foresight + Scene Hint
    in a single LLM call.

    Args:
        segment_dialogue: Raw dialogue text from the segment.
        current_date: Date context for temporal anchoring.
        prior_facts: List of fact strings from previously extracted segments,
                     used for cross-reference resolution (e.g., "that book you recommended").

    Returns:
        {"episode": str, "atomic_facts": list[str], "foresight": list[dict], "scene_hint": dict}
    """
    if current_date is None:
        IST = timezone(timedelta(hours=5, minutes=30))
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    # Build prior context block from episode summaries
    if prior_facts:
        summaries = "\n".join(f"- {s}" for s in prior_facts[-20:])  # last 20 episode summaries
        prior_block = (
            "PRIOR CONTEXT (episode summaries from earlier in this conversation — use these to resolve "
            "references like 'that book you recommended', 'the place you mentioned', etc.):\n"
            f"{summaries}"
        )
    else:
        prior_block = ""

    prompt = (_load_prompt()
              .replace("{prior_facts_block}", prior_block)
              .replace("{segment}", segment_dialogue)
              .replace("{current_date}", current_date))
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    parsed = json.loads(text)
    return parsed
