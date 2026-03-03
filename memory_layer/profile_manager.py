import json
import os
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
from models import Conflict, AtomicFact
import db
import vector_store
from agentic_layer.vectorize_service import embed_text

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "conflict_detection.txt")

# Facts with similarity above this threshold are checked for contradiction.

CONFLICT_SIMILARITY_THRESHOLD = 0.65


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def _check_contradictions_batch(new_fact: str, existing_facts: list[str]) -> list[dict]:
    """Check a new fact against multiple existing facts in a single LLM call."""
    prompt_template = _load_prompt()
    candidate_list = "\n".join(
        f'[{i}] "{fact}"' for i, fact in enumerate(existing_facts)
    )
    prompt = prompt_template.replace("{new_fact}", new_fact).replace("{candidate_list}", candidate_list)

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    parsed = json.loads(text)
    return parsed.get("results", [])


def _ask_user_resolution(old_fact_text: str, new_fact_text: str, reasoning: str) -> str:
    """
    Prompt the user to resolve a detected conflict.

    Returns one of: 'keep_new', 'keep_old', 'keep_both', or a custom corrected fact string.
    """
    print(f"\n       ╔══ CONFLICT DETECTED ══════════════════════════════")
    print(f"       ║ Old: {old_fact_text}")
    print(f"       ║ New: {new_fact_text}")
    print(f"       ║ Why: {reasoning}")
    print(f"       ╠═════════════════════════════════════════════════════")
    print(f"       ║ [1] Keep NEW fact, deactivate old (recency wins)")
    print(f"       ║ [2] Keep OLD fact, discard new")
    print(f"       ║ [3] Keep BOTH (no conflict)")
    print(f"       ║ [4] Enter a CORRECTED fact to replace both")
    print(f"       ╚═════════════════════════════════════════════════════")

    while True:
        choice = input("       Your choice (1/2/3/4): ").strip()
        if choice == "1":
            return "keep_new"
        elif choice == "2":
            return "keep_old"
        elif choice == "3":
            return "keep_both"
        elif choice == "4":
            corrected = input("       Enter corrected fact: ").strip()
            if corrected:
                return corrected
            print("       (empty input, try again)")
        else:
            print("       (invalid choice, enter 1-4)")


def detect_conflicts(new_fact_id: int, new_fact_text: str, new_fact_embedding: list[float],
                     interactive: bool = False, current_date: str = None) -> list[int]:
    """
    Check if a newly inserted fact contradicts any existing facts.

    Args:
        new_fact_id: ID of the newly inserted fact
        new_fact_text: Text of the new fact
        new_fact_embedding: Pre-computed embedding of the new fact
        interactive: If True, ask the user how to resolve each conflict.
                     If False, auto-resolve with recency wins.
        current_date: Date of the conversation that produced this fact (YYYY-MM-DD).
                      Used to set superseded_on when deactivating old facts.

    Returns:
        List of old_fact_ids that were detected as conflicts.
    """
    # Hybrid candidate selection: vector search + keyword search
    # Vector search catches semantically similar facts (e.g. "is vegetarian" vs "went vegan")
    # Keyword search catches structurally similar facts with different entities
    #   (e.g. "works at TechCorp" vs "works at Google" — shared verb, different company)
    seen_fact_ids = {new_fact_id}

    # Path 1: Vector similarity
    similar = vector_store.search_facts(new_fact_embedding, top_k=5)
    vector_candidates = [
        hit for hit in similar
        if hit["fact_id"] not in seen_fact_ids and hit["score"] >= CONFLICT_SIMILARITY_THRESHOLD
    ]
    for hit in vector_candidates:
        seen_fact_ids.add(hit["fact_id"])

    # Path 2: Keyword (full-text) search
    keyword_hits = db.keyword_search_facts(new_fact_text, top_k=5)
    keyword_candidates = [
        {"fact_id": row["id"], "memcell_id": row["memcell_id"], "score": float(row["rank"])}
        for row in keyword_hits
        if row["id"] not in seen_fact_ids
    ]

    merged_candidates = vector_candidates + keyword_candidates

    if not merged_candidates:
        return []

    # Collect all active candidate facts before making the LLM call
    active_candidates = []
    for candidate in merged_candidates:
        old_fact = db.get_fact_by_id(candidate["fact_id"])
        if old_fact and old_fact["is_active"]:
            active_candidates.append({"hit": candidate, "old_fact": old_fact})

    if not active_candidates:
        return []

    # Single batched LLM call for all candidates
    existing_fact_texts = [c["old_fact"]["fact_text"] for c in active_candidates]
    batch_results = _check_contradictions_batch(new_fact_text, existing_fact_texts)

    conflicted_ids = []

    for result in batch_results:
        idx = result.get("existing_fact_index")
        if idx is None or idx < 0 or idx >= len(active_candidates):
            continue
        if not result.get("is_contradiction", False):
            continue

        candidate = active_candidates[idx]["hit"]
        old_fact = active_candidates[idx]["old_fact"]
        reasoning = result.get("reasoning", "N/A")

        if interactive:
            resolution = _ask_user_resolution(old_fact["fact_text"], new_fact_text, reasoning)
        else:
            resolution = "keep_new"

        if resolution == "keep_new":
            conflict = Conflict(
                old_fact_id=candidate["fact_id"],
                new_fact_id=new_fact_id,
                resolution="recency_wins",
            )
            db.insert_conflict(conflict)
            db.deactivate_fact(candidate["fact_id"], superseded_on=current_date)
            conflicted_ids.append(candidate["fact_id"])
            print(f"       CONFLICT RESOLVED: '{old_fact['fact_text']}' → superseded by '{new_fact_text}'")

        elif resolution == "keep_old":
            conflict = Conflict(
                old_fact_id=candidate["fact_id"],
                new_fact_id=new_fact_id,
                resolution="keep_old",
            )
            db.insert_conflict(conflict)
            db.deactivate_fact(new_fact_id, superseded_on=current_date)
            conflicted_ids.append(new_fact_id)
            print(f"       CONFLICT RESOLVED: Kept '{old_fact['fact_text']}', discarded new")

        elif resolution == "keep_both":
            conflict = Conflict(
                old_fact_id=candidate["fact_id"],
                new_fact_id=new_fact_id,
                resolution="keep_both",
            )
            db.insert_conflict(conflict)
            print(f"       CONFLICT LOGGED: Both facts kept active")

        else:
            db.deactivate_fact(candidate["fact_id"], superseded_on=current_date)
            db.deactivate_fact(new_fact_id, superseded_on=current_date)

            corrected_fact = AtomicFact(memcell_id=old_fact["memcell_id"], fact_text=resolution,
                                        conversation_date=current_date)
            corrected_id = db.insert_atomic_fact(corrected_fact)
            corrected_embedding = embed_text(resolution)
            vector_store.upsert_fact(corrected_id, old_fact["memcell_id"], corrected_embedding)

            conflict = Conflict(
                old_fact_id=candidate["fact_id"],
                new_fact_id=new_fact_id,
                resolution=f"corrected:{corrected_id}",
            )
            db.insert_conflict(conflict)
            conflicted_ids.append(candidate["fact_id"])
            print(f"       CONFLICT RESOLVED: Both replaced with '{resolution}'")

    return conflicted_ids
