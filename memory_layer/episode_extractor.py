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
                    conversation_summary: str = None) -> dict:
    """
    Given raw dialogue from a segment, produce Episode + Atomic Facts + Foresight + Scene Hint
    in a single LLM call.

    Args:
        segment_dialogue: Raw dialogue text from the segment.
        current_date: Date context for temporal anchoring.
        conversation_summary: Rolling summary of the full conversation history,
                              used for cross-reference resolution.

    Returns:
        {"episode": str, "atomic_facts": list[dict], "foresight": list[dict], "scene_hint": dict}
    """
    if current_date is None:
        IST = timezone(timedelta(hours=5, minutes=30))
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    # Build conversation summary block
    if conversation_summary:
        summary_block = (
            "CONVERSATION SUMMARY (compressed history of everything before this segment — "
            "use this to resolve references like 'that book you recommended', "
            "'the place you mentioned', etc.):\n"
            f"{conversation_summary}"
        )
    else:
        summary_block = ""

    prompt = (_load_prompt()
              .replace("{conversation_summary_block}", summary_block)
              .replace("{segment}", segment_dialogue)
              .replace("{current_date}", current_date))
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

    def _parse_response(resp_text: str) -> dict:
        text = resp_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        return json.loads(text)

    try:
        return _parse_response(response.text)
    except json.JSONDecodeError:
        # Retry once on malformed JSON
        print(f"  [extract] JSON parse failed, retrying...")
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return _parse_response(response.text)


def process_segment(seg: dict, current_date: str, conversation_summary: str = None) -> dict:
    """Extract episode, facts, foresight, and metadata for one segment (single LLM call)."""
    result = extract_episode(seg["dialogue"], current_date, conversation_summary=conversation_summary)
    return {
        "segment": seg,
        "episode_text": result["episode"],
        "atomic_facts": result["atomic_facts"],
        "foresight": result.get("foresight", []),
        "metadata": result.get("metadata", {}),
    }
