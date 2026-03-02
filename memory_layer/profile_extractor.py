import json
import os
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
from models import UserProfile
import db

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "profile_extraction.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def update_user_profile():
    """
    Rebuild user profile from active facts only.

    Reads existing profile (if any), gathers all active (non-superseded)
    facts, and asks Gemini to produce an updated compact profile.
    """
    # Get existing profile
    existing = db.get_user_profile()
    if existing:
        prev_profile = json.dumps({
            "explicit_facts": existing.explicit_facts,
            "implicit_traits": existing.implicit_traits,
        }, indent=2)
    else:
        prev_profile = "None (first time creating profile)"

    # Get all active facts (source of truth — superseded facts are excluded)
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT fact_text FROM atomic_facts WHERE is_active = TRUE ORDER BY created_at DESC")
    facts = cur.fetchall()
    cur.close()
    conn.close()

    if not facts:
        return

    facts_text = "\n".join(
        f"- {fact_text}" for (fact_text,) in facts
    )

    prompt_template = _load_prompt()
    prompt = prompt_template.replace("{previous_profile}", prev_profile).replace("{active_facts}", facts_text)

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    parsed = json.loads(text)

    profile = UserProfile(
        explicit_facts=parsed.get("explicit_facts", []),
        implicit_traits=parsed.get("implicit_traits", []),
    )
    db.upsert_user_profile(profile)

    print(f"       Profile updated: {len(profile.explicit_facts)} explicit facts, {len(profile.implicit_traits)} implicit traits")
    return profile
