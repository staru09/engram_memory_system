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
                    conversation_summary: str = None, prior_facts: list[str] = None) -> dict:
    """
    Given raw dialogue from a segment, produce Episode + Atomic Facts + Foresight + Scene Hint
    in a single LLM call.

    Args:
        segment_dialogue: Raw dialogue text from the segment.
        current_date: Date context for temporal anchoring.
        conversation_summary: Rolling summary of the entire conversation so far.
        prior_facts: (Deprecated) List of episode summaries — use conversation_summary instead.

    Returns:
        {"episode": str, "atomic_facts": list[str], "foresight": list[dict], "scene_hint": dict}
    """
    if current_date is None:
        IST = timezone(timedelta(hours=5, minutes=30))
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    # Build conversation summary block (replaces prior episode summaries)
    if conversation_summary:
        summary_block = (
            "CONVERSATION SUMMARY (compressed history of everything before this segment — "
            "use to resolve references like 'that book you recommended', 'the place you mentioned', etc.):\n"
            f"{conversation_summary}"
        )
    elif prior_facts:
        # Fallback: use prior episode summaries if no conversation summary available
        summaries = "\n".join(f"- {s}" for s in prior_facts[-20:])
        summary_block = (
            "PRIOR CONTEXT (episode summaries from earlier):\n"
            f"{summaries}"
        )
    else:
        summary_block = ""

    prompt = (_load_prompt()
              .replace("{prior_facts_block}", summary_block)
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
