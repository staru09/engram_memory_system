import json
import os
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
from models import UserProfile
import db
from agentic_layer.vectorize_service import embed_text

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "profile_extraction.txt")
_CATEGORY_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "category_profile.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def _load_category_prompt():
    with open(_CATEGORY_PROMPT_PATH) as f:
        return f.read()


def update_user_profile(new_facts: list[str] = None):
    """
    Update user profile incrementally with new facts from current batch.
    Falls back to full rebuild if no new_facts provided (first time / migration).
    """
    existing = db.get_user_profile()
    if existing:
        prev_profile = json.dumps({
            "explicit_facts": existing.explicit_facts,
            "implicit_traits": existing.implicit_traits,
        }, indent=2)
    else:
        prev_profile = "None (first time creating profile)"

    if new_facts:
        facts_text = "\n".join(f"- {f}" for f in new_facts)
    else:
        # Fallback: full rebuild (first time or migration)
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT fact_text FROM atomic_facts WHERE is_active = TRUE ORDER BY created_at DESC")
        facts = cur.fetchall()
        cur.close()
        db.release_connection(conn)
        if not facts:
            return
        facts_text = "\n".join(f"- {fact_text}" for (fact_text,) in facts)

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


def _update_single_category(cat_name: str, new_facts: list[str], prompt_template: str) -> str | None:
    """Update a single category profile incrementally. Returns cat_name on success, None on skip."""
    if not new_facts:
        return None

    existing = db.get_profile_category(cat_name)
    prev_summary = existing["summary_text"] if existing and existing.get("summary_text") else "None (first time)"

    facts_text = "\n".join(f"- {f}" for f in new_facts)
    prompt = prompt_template.replace("{category_name}", cat_name).replace(
        "{previous_summary}", prev_summary
    ).replace("{facts_list}", facts_text)

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    summary = response.text.strip()

    summary_embedding = embed_text(summary)
    new_count = (existing.get("fact_count", 0) if existing else 0) + len(new_facts)
    db.upsert_profile_category(cat_name, summary, new_count, summary_embedding)
    print(f"       [{cat_name}] +{len(new_facts)} new facts → {len(summary)} chars")
    return cat_name


def update_category_profiles(new_facts_by_category: dict[str, list[str]]):
    """Update category profiles incrementally with only new facts, in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not new_facts_by_category:
        return

    prompt_template = _load_category_prompt()

    with ThreadPoolExecutor(max_workers=len(new_facts_by_category)) as executor:
        futures = {
            executor.submit(_update_single_category, cat, facts, prompt_template): cat
            for cat, facts in new_facts_by_category.items()
        }
        updated = sum(1 for f in as_completed(futures) if f.result() is not None)

    print(f"       Category profiles updated: {updated}/{len(new_facts_by_category)} categories")


def rebuild_conversation_summary(new_episodes: list[str], current_date: str):
    """Rebuild the rolling conversation summary after ingesting new episodes."""
    existing_summary = db.get_conversation_summary()

    if not new_episodes:
        return

    episodes_text = "\n".join(f"- {ep}" for ep in new_episodes)

    if existing_summary:
        prompt = f"""You are maintaining a rolling summary of a long-term conversation between two people.

EXISTING SUMMARY:
{existing_summary}

NEW EPISODES (from session dated {current_date}):
{episodes_text}

Update the summary to incorporate the new episodes. Keep it concise (10-15 sentences max).
Focus on: key facts about each person, their relationship, ongoing plans, recent events, and any changes.
Drop details that are no longer relevant (cancelled plans, resolved issues).

Return ONLY the updated summary text, no JSON or formatting."""
    else:
        prompt = f"""You are creating a summary of a conversation between two people.

EPISODES (from session dated {current_date}):
{episodes_text}

Write a concise summary (10-15 sentences max).
Focus on: key facts about each person, their relationship, plans, events, and preferences.

Return ONLY the summary text, no JSON or formatting."""

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        summary = response.text.strip()
        db.upsert_conversation_summary(summary)
        print(f"  [summary] Updated ({len(summary)} chars)")
    except Exception as e:
        print(f"  [summary] Update failed: {e}")


def build_session_summary(episodes: list[str], current_date: str,
                          source_id: str):
    """Build and store a per-session summary with embedding."""
    if not episodes:
        return

    episodes_text = "\n".join(f"- {ep}" for ep in episodes)
    prompt = f"""Summarize this conversation session in 3-5 sentences. Focus on key events, decisions, and new information shared.

EPISODES (from session dated {current_date}):
{episodes_text}

Return ONLY the summary text, no JSON or formatting."""

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        summary = response.text.strip()
        from agentic_layer.vectorize_service import embed_text
        summary_embedding = embed_text(summary)
        db.insert_session_summary(source_id, current_date, summary, summary_embedding)
        print(f"  [session-summary] Stored ({len(summary)} chars)")
    except Exception as e:
        print(f"  [session-summary] Failed: {e}")
