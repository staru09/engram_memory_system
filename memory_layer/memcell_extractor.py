import json
import os
from datetime import timezone, timedelta
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "segmentation.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def segment_conversation(conversation: list[dict]) -> list[dict]:
    """
    Split a conversation into topical segments.

    Args:
        conversation: List of {"role": "user"|"assistant", "content": "..."} dicts.

    Returns:
        List of segment dicts with keys: segment_id, start_turn, end_turn, topic_hint.
    """
    conv_text = "\n".join(
        f"[Turn {i}] {turn['role']}: {turn['content']}"
        for i, turn in enumerate(conversation)
    )

    prompt = _load_prompt().replace("{conversation}", conv_text)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

    text = response.text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    return json.loads(text)


def extract_segments(conversation: list[dict], extract_all_speakers: bool = False) -> list[dict]:
    """
    Segment conversation and return the raw dialogue text for each segment.

    Args:
        extract_all_speakers: If True, treat all speakers equally (no [CONTEXT ONLY] tag).

    Returns:
        List of {"segment_id": int, "topic_hint": str, "dialogue": str, "turns": list[dict]}
    """
    segments = segment_conversation(conversation)
    results = []
    for seg in segments:
        start = seg["start_turn"]
        end = seg["end_turn"]
        turns = conversation[start:end + 1]
        def _format_turn(t):
            IST = timezone(timedelta(hours=5, minutes=30))
            ts = t.get("created_at")
            time_prefix = ""
            if ts and hasattr(ts, 'strftime'):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                time_prefix = f"[{ts.astimezone(IST).strftime('%H:%M')}] "
            if t['role'] == 'user' or extract_all_speakers:
                return f"{time_prefix}{t['role']}: {t['content']}"
            else:
                return f"{time_prefix}{t['role']} [CONTEXT ONLY]: {t['content']}"

        dialogue = "\n".join(_format_turn(t) for t in turns)
        results.append({
            "segment_id": seg["segment_id"],
            "topic_hint": seg["topic_hint"],
            "dialogue": dialogue,
            "turns": turns,
        })
    return results
