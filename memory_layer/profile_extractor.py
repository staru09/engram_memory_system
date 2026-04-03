import json
import os
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL, PROFILE_TOKEN_BUDGET, COMPRESSION_THRESHOLD
import db

client = genai.Client(api_key=GEMINI_API_KEY)

_PROFILE_UPDATE_PATH = os.path.join(os.path.dirname(__file__), "prompts", "profile_update.txt")
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


def _number_profile_lines(profile: str) -> str:
    """Add line numbers to profile for LLM reference.

    Returns numbered profile text like:
      [1] - Rampal is 24 years old
      [2] - Rampal works as an MLE
      ...
      [15] ## IMPLICIT TRAITS
      [16] - [Health-Conscious] — goes to gym 6 days a week
    """
    lines = profile.split("\n")
    numbered = []
    for i, line in enumerate(lines, 1):
        numbered.append(f"[{i}] {line}")
    return "\n".join(numbered)


def _apply_operations(profile: str, operations: list[dict]) -> str:
    """Apply add/update/delete operations to profile text programmatically."""
    lines = profile.split("\n")

    # Collect deletes and updates by line number
    deletes = set()
    updates = {}
    adds_explicit = []
    adds_implicit = []

    for op in operations:
        action = op.get("action", "none")
        if action == "none":
            continue
        elif action == "delete":
            line_num = op.get("line")
            if line_num and 1 <= line_num <= len(lines):
                deletes.add(line_num)
                print(f"    [op] DELETE line {line_num}: {lines[line_num - 1].strip()}")
        elif action == "update":
            line_num = op.get("line")
            new_fact = op.get("new_fact", "")
            if line_num and 1 <= line_num <= len(lines) and new_fact:
                updates[line_num] = new_fact
                print(f"    [op] UPDATE line {line_num}: {lines[line_num - 1].strip()} → {new_fact.strip()}")
        elif action == "add":
            fact = op.get("fact", "")
            section = op.get("section", "explicit")
            if fact:
                if section == "implicit":
                    adds_implicit.append(fact)
                else:
                    adds_explicit.append(fact)
                print(f"    [op] ADD ({section}): {fact.strip()}")

    # Apply updates and deletes
    new_lines = []
    for i, line in enumerate(lines, 1):
        if i in deletes:
            continue
        elif i in updates:
            new_lines.append(updates[i])
        else:
            new_lines.append(line)

    # Find the implicit traits header to know where to insert
    implicit_idx = None
    for i, line in enumerate(new_lines):
        if "## IMPLICIT TRAITS" in line:
            implicit_idx = i
            break

    # Add new explicit facts (before ## IMPLICIT TRAITS, or at the end)
    if adds_explicit:
        if implicit_idx is not None:
            # Insert before the blank line preceding ## IMPLICIT TRAITS
            insert_at = implicit_idx
            # Skip back over blank lines
            while insert_at > 0 and new_lines[insert_at - 1].strip() == "":
                insert_at -= 1
            for fact in reversed(adds_explicit):
                new_lines.insert(insert_at, fact)
            # Recalculate implicit_idx after insertion
            implicit_idx = None
            for i, line in enumerate(new_lines):
                if "## IMPLICIT TRAITS" in line:
                    implicit_idx = i
                    break
        else:
            # No implicit section yet — append at end
            for fact in adds_explicit:
                new_lines.append(fact)

    # Add new implicit traits (at the end)
    if adds_implicit:
        if implicit_idx is None:
            # Create the section
            new_lines.append("")
            new_lines.append("## IMPLICIT TRAITS")
        for fact in adds_implicit:
            new_lines.append(fact)

    return "\n".join(new_lines)


def update_user_profile(new_facts: list[dict]) -> tuple[str, list[dict]]:
    """Update profile with new facts using add/update/delete operations.

    The LLM outputs structured operations instead of rewriting the full profile.
    Operations are applied programmatically for predictability.

    Args:
        new_facts: list of {"text": ..., "category": ..., "date": ...}

    Returns:
        (updated_profile_text, conflicts_list)
    """
    if not new_facts:
        return db.get_user_profile(), []

    existing_profile = db.get_and_upsert_profile()
    is_first_update = not existing_profile

    # For the LLM prompt, show a placeholder so it knows to only add
    prompt_profile = existing_profile if existing_profile else "(Empty — first update. Use only 'add' operations.)"

    # Number lines so LLM can reference them
    numbered_profile = _number_profile_lines(prompt_profile)

    # Format new facts grouped by category
    facts_text = "\n".join(
        f"- [{f['category']}] ({f.get('date', 'unknown')}) {f['text']}"
        for f in new_facts
    )

    prompt = _load_prompt(_PROFILE_UPDATE_PATH)
    prompt = prompt.replace("{existing_profile}", numbered_profile)
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
            operations = parsed.get("operations", [])
            conflicts = parsed.get("conflicts", [])

            # Check if no-op
            if len(operations) == 1 and operations[0].get("action") == "none":
                print(f"  [profile] No changes needed")
                return existing_profile, []

            # Apply operations programmatically (use empty base on first update)
            base_profile = "" if is_first_update else existing_profile
            updated_profile = _apply_operations(base_profile, operations)

            # Store profile + log conflicts in single connection
            db.get_and_upsert_profile(new_profile=updated_profile, conflicts=conflicts)

            op_summary = ", ".join(
                f"{sum(1 for o in operations if o.get('action') == a)} {a}"
                for a in ["add", "update", "delete"]
                if any(o.get("action") == a for o in operations)
            )
            print(f"  [profile] Updated ({op_summary}, {len(conflicts)} conflicts, {len(updated_profile)} chars)")
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


