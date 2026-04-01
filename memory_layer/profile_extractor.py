import json
import os
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL, PROFILE_TOKEN_BUDGET, SUMMARY_TOKEN_BUDGET, COMPRESSION_THRESHOLD
from models import ConflictLog
import db

client = genai.Client(api_key=GEMINI_API_KEY)

_PROFILE_UPDATE_PATH = os.path.join(os.path.dirname(__file__), "prompts", "profile_update.txt")
_SUMMARY_COMPRESS_PATH = os.path.join(os.path.dirname(__file__), "prompts", "summary_compression.txt")
_PROFILE_COMPRESS_PATH = os.path.join(os.path.dirname(__file__), "prompts", "profile_compression.txt")


def _load_prompt(path):
    with open(path) as f:
        return f.read()


def _count_tokens(text: str) -> int:
    """Count tokens using Gemini API."""
    try:
        result = client.models.count_tokens(
            model=GEMINI_MODEL,
            contents=text
        )
        return result.total_tokens
    except Exception as e:
        # Fallback: ~3.5 chars per token (more conservative)
        fallback = max(1, int(len(text) / 3.5))
        print(f"  [tokens] API failed ({e}), using fallback: {fallback}")
        return fallback


def update_user_profile(new_facts: list[dict]) -> tuple[str, list[dict]]:
    """Update profile with new facts and detect conflicts.

    Args:
        new_facts: list of {"text": ..., "category": ..., "date": ...}

    Returns:
        (updated_profile_text, conflicts_list)
    """
    if not new_facts:
        return db.get_user_profile(), []

    existing_profile = db.get_user_profile()
    if not existing_profile:
        existing_profile = "(No profile yet — this is the first update.)"

    # Format new facts grouped by category
    facts_text = "\n".join(
        f"- [{f['category']}] ({f.get('date', 'unknown')}) {f['text']}"
        for f in new_facts
    )

    prompt = _load_prompt(_PROFILE_UPDATE_PATH)
    prompt = prompt.replace("{existing_profile}", existing_profile)
    prompt = prompt.replace("{new_facts}", facts_text)

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            if response.text is None:
                if attempt < 2:
                    import time; time.sleep(2)
                    continue
                return existing_profile, []

            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]

            parsed = json.loads(text)
            updated_profile = parsed.get("updated_profile", existing_profile)
            conflicts = parsed.get("conflicts", [])

            # Store profile
            db.upsert_user_profile(updated_profile)

            # Log conflicts
            for c in conflicts:
                db.insert_conflict_log(ConflictLog(
                    category=c.get("category", "unknown"),
                    old_value=c.get("old_value", ""),
                    new_value=c.get("new_value", ""),
                ))

            print(f"  [profile] Updated ({len(updated_profile)} chars, {len(conflicts)} conflicts)")
            return updated_profile, conflicts

        except json.JSONDecodeError as e:
            if attempt < 2:
                print(f"  [profile] JSON parse error ({e}), retrying...")
                import time; time.sleep(2)
                continue
            print(f"  [profile] Failed after 3 attempts: {e}")
            return existing_profile, []
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(2)
                continue
            print(f"  [profile] Failed: {e}")
            return existing_profile, []


def maybe_compress_profile():
    """Compress profile if it exceeds 80% of token budget."""
    profile = db.get_user_profile()
    if not profile:
        return

    token_count = _count_tokens(profile)
    threshold = int(PROFILE_TOKEN_BUDGET * COMPRESSION_THRESHOLD)

    if token_count < threshold:
        return

    print(f"  [profile] Compressing ({token_count} tokens, threshold {threshold})...")
    prompt = _load_prompt(_PROFILE_COMPRESS_PATH)
    prompt = prompt.replace("{current_profile}", profile)

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        if response.text:
            compressed = response.text.strip()
            db.upsert_user_profile(compressed)
            new_count = _count_tokens(compressed)
            print(f"  [profile] Compressed: {token_count} → {new_count} tokens")
    except Exception as e:
        print(f"  [profile] Compression failed: {e}")


def append_to_rolling_summary(facts: list[dict], current_date: str):
    """Append new date-tagged entries to the Recent section of rolling summary."""
    if not facts:
        return

    # Format facts as date-tagged entries
    new_entries = []
    for f in facts:
        date = f.get("date", current_date)
        new_entries.append(f"[{date}] {f['text']}")
    new_text = "\n".join(new_entries)

    # Get current summary
    summary = db.get_conversation_summary()
    archive = summary["archive_text"]
    recent = summary["recent_text"]

    # Append to recent
    if recent:
        recent = recent + "\n" + new_text
    else:
        recent = new_text

    # Count tokens
    total_text = archive + "\n" + recent if archive else recent
    token_count = _count_tokens(total_text)

    db.upsert_conversation_summary(archive, recent, token_count)
    print(f"  [summary] Appended {len(new_entries)} entries to Recent ({token_count} tokens)")


ARCHIVE_MAX_RATIO = 0.20  # archive should be ≤20% of total budget


def maybe_compress_summary():
    """Compress rolling summary if it exceeds 80% of token budget.
    Also cap archive at 20% of budget — re-compress if needed."""
    summary = db.get_conversation_summary()
    token_count = summary["token_count"]
    threshold = int(SUMMARY_TOKEN_BUDGET * COMPRESSION_THRESHOLD)

    if token_count < threshold:
        return

    archive = summary["archive_text"]
    recent = summary["recent_text"]

    if not recent:
        return

    # Split recent into lines, take oldest ~40% for compression
    recent_lines = recent.strip().split("\n")
    if len(recent_lines) <= 2:
        return

    split_point = max(1, len(recent_lines) * 2 // 5)
    entries_to_compress = "\n".join(recent_lines[:split_point])
    remaining_recent = "\n".join(recent_lines[split_point:])

    print(f"  [summary] Compressing ({token_count} tokens, threshold {threshold})...")
    print(f"  [summary] Moving {split_point}/{len(recent_lines)} entries to archive")

    prompt = _load_prompt(_SUMMARY_COMPRESS_PATH)
    prompt = prompt.replace("{existing_archive}", archive if archive else "(Empty — first compression)")
    prompt = prompt.replace("{entries_to_compress}", entries_to_compress)

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        if response.text:
            new_archive = response.text.strip()

            # Check if archive exceeds 20% cap
            archive_max_tokens = int(SUMMARY_TOKEN_BUDGET * ARCHIVE_MAX_RATIO)
            archive_tokens = _count_tokens(new_archive)

            if archive_tokens > archive_max_tokens:
                print(f"  [summary] Archive too large ({archive_tokens} tokens, cap {archive_max_tokens}), re-compressing...")
                recompress_prompt = _load_prompt(_SUMMARY_COMPRESS_PATH)
                recompress_prompt = recompress_prompt.replace("{existing_archive}", "(Condense this into a shorter summary)")
                recompress_prompt = recompress_prompt.replace("{entries_to_compress}", new_archive)
                try:
                    recompress_response = client.models.generate_content(model=GEMINI_MODEL, contents=recompress_prompt)
                    if recompress_response.text:
                        new_archive = recompress_response.text.strip()
                        archive_tokens = _count_tokens(new_archive)
                        print(f"  [summary] Archive re-compressed to {archive_tokens} tokens")
                except Exception as e:
                    print(f"  [summary] Archive re-compression failed: {e}")

            total_text = new_archive + "\n" + remaining_recent
            new_token_count = _count_tokens(total_text)
            db.upsert_conversation_summary(new_archive, remaining_recent, new_token_count)
            print(f"  [summary] Compressed: {token_count} → {new_token_count} tokens (archive: {archive_tokens}, recent: {new_token_count - archive_tokens})")
    except Exception as e:
        print(f"  [summary] Compression failed: {e}")
