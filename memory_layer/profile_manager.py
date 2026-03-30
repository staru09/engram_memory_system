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
_BATCH_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "conflict_detection_batch.txt")

# Facts with similarity above this threshold are checked for contradiction.

CONFLICT_SIMILARITY_THRESHOLD = 0.75


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
    similar = vector_store.search_facts(new_fact_embedding, top_k=3)
    vector_candidates = [
        hit for hit in similar
        if hit["fact_id"] not in seen_fact_ids and hit["score"] >= CONFLICT_SIMILARITY_THRESHOLD
    ]
    for hit in vector_candidates:
        seen_fact_ids.add(hit["fact_id"])

    # Path 2: Keyword (full-text) search
    keyword_hits = db.keyword_search_facts(new_fact_text, top_k=3)
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


def _find_candidates_for_fact(fact_id: int, fact_text: str, fact_embedding: list[float]) -> list[dict]:
    """Find conflict candidates for a single fact via hybrid search."""
    seen = {fact_id}

    # Vector search
    similar = vector_store.search_facts(fact_embedding, top_k=3)
    vector_cands = [
        hit for hit in similar
        if hit["fact_id"] not in seen and hit["score"] >= CONFLICT_SIMILARITY_THRESHOLD
    ]
    for hit in vector_cands:
        seen.add(hit["fact_id"])

    # Keyword search
    keyword_hits = db.keyword_search_facts(fact_text, top_k=3)
    keyword_cands = [
        {"fact_id": row["id"], "memcell_id": row["memcell_id"], "score": float(row["rank"])}
        for row in keyword_hits
        if row["id"] not in seen
    ]

    merged = vector_cands + keyword_cands

    # Batch fetch all candidate facts in a single DB call
    candidate_ids = [cand["fact_id"] for cand in merged]
    facts_map = db.get_facts_by_ids(candidate_ids)

    # Keep only active
    active = []
    for cand in merged:
        old_fact = facts_map.get(cand["fact_id"])
        if old_fact and old_fact["is_active"]:
            active.append({"fact_id": cand["fact_id"], "fact_text": old_fact["fact_text"]})
    return active


CONFLICT_BATCH_SIZE = 25


def _check_contradictions_batch_pairs(pairs: list[dict]) -> list[dict]:
    """Check multiple new-fact-vs-old-fact pairs. Splits into chunks if too many."""
    if not pairs:
        return []

    all_results = []
    for chunk_start in range(0, len(pairs), CONFLICT_BATCH_SIZE):
        chunk = pairs[chunk_start:chunk_start + CONFLICT_BATCH_SIZE]
        chunk_results = _check_contradictions_chunk(chunk, offset=chunk_start)
        all_results.extend(chunk_results)

    return all_results


def _check_contradictions_chunk(pairs: list[dict], offset: int = 0) -> list[dict]:
    """Single LLM call for a chunk of pairs. Retries once on JSON failure."""
    prompt_template = _load_prompt_file(_BATCH_PROMPT_PATH)
    pair_lines = []
    for i, p in enumerate(pairs):
        pair_lines.append(f'[{i}] New: "{p["new_fact_text"]}" | Existing: "{p["old_fact_text"]}"')
    pair_list = "\n".join(pair_lines)
    prompt = prompt_template.replace("{pair_list}", pair_list)

    def _parse(resp_text):
        text = resp_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        parsed = json.loads(text)
        results = parsed.get("results", [])
        for r in results:
            if "pair_index" in r:
                r["pair_index"] += offset
        return results

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return _parse(response.text)
    except json.JSONDecodeError:
        print(f"       [conflict] JSON parse failed, retrying chunk ({len(pairs)} pairs)...")
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            return _parse(response.text)
        except Exception as e:
            print(f"       [conflict] Retry failed ({e}), skipping chunk")
            return []


def _load_prompt_file(path):
    with open(path) as f:
        return f.read()


def _find_all_candidates_batched(facts_with_embeddings: list[dict],
                                 exclude_ids: set = None) -> tuple[list[dict], list[dict]]:
    """
    Find conflict candidates for ALL facts with minimal DB round-trips.
    Excludes facts in exclude_ids (current batch) from candidate search at the query level.

    Returns (all_pairs, pair_metadata) ready for the LLM call.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_pairs = []
    pair_metadata = []

    if exclude_ids is None:
        exclude_ids = set()

    # Run vector + keyword searches in parallel across all facts
    search_start = time.time()

    def _search_one_fact(new_fact):
        """Run vector + keyword search for one fact, return raw candidates."""
        # Vector search — exclude batch facts at Qdrant level
        similar = vector_store.search_facts(new_fact["embedding"], top_k=3,
                                            exclude_ids=exclude_ids)
        vector_cands = [
            hit for hit in similar
            if hit["score"] >= CONFLICT_SIMILARITY_THRESHOLD
        ]
        seen = {hit["fact_id"] for hit in vector_cands}

        # Keyword search — exclude batch facts at SQL level
        keyword_hits = db.keyword_search_facts(new_fact["fact_text"], top_k=3,
                                               exclude_ids=exclude_ids)
        keyword_cands = [
            {"fact_id": row["id"], "memcell_id": row["memcell_id"], "score": float(row["rank"])}
            for row in keyword_hits
            if row["id"] not in seen
        ]

        return {"new_fact": new_fact, "candidates": vector_cands + keyword_cands}

    # Parallel search across all facts in the segment
    search_results = []
    with ThreadPoolExecutor(max_workers=min(len(facts_with_embeddings), 10)) as executor:
        futures = {executor.submit(_search_one_fact, f): i for i, f in enumerate(facts_with_embeddings)}
        search_results = [None] * len(facts_with_embeddings)
        for future in as_completed(futures):
            idx = futures[future]
            search_results[idx] = future.result()

    print(f"       [conflict] Candidate search: {time.time() - search_start:.1f}s")

    # Collect all unique candidate fact IDs, batch-fetch from DB in one call
    all_candidate_ids = set()
    for sr in search_results:
        for cand in sr["candidates"]:
            all_candidate_ids.add(cand["fact_id"])

    fetch_start = time.time()
    facts_map = db.get_facts_by_ids(list(all_candidate_ids))
    print(f"       [conflict] Batch fact fetch ({len(all_candidate_ids)} facts): {time.time() - fetch_start:.1f}s")

    # Build pairs using the batch-fetched data
    for sr in search_results:
        new_fact = sr["new_fact"]
        for cand in sr["candidates"]:
            old_fact = facts_map.get(cand["fact_id"])
            if old_fact and old_fact["is_active"]:
                all_pairs.append({
                    "new_fact_text": new_fact["fact_text"],
                    "old_fact_text": old_fact["fact_text"],
                })
                pair_metadata.append({
                    "new_fact_id": new_fact["fact_id"],
                    "new_fact_text": new_fact["fact_text"],
                    "old_fact_id": cand["fact_id"],
                    "old_fact_text": old_fact["fact_text"],
                })

    return all_pairs, pair_metadata


def detect_conflicts_batch(facts_with_embeddings: list[dict],
                           interactive: bool = False,
                           current_date: str = None,
                           exclude_ids: set = None) -> int:
    """
    Batch conflict detection for all facts in one call.

    Args:
        facts_with_embeddings: list of {fact_id, fact_text, embedding}
        interactive: user-resolution mode
        current_date: for superseded_on dating
        exclude_ids: fact IDs to exclude from candidate search (current batch)

    Returns:
        Total number of conflicts detected.
    """
    import time

    # Step 1: Parallel candidate search + single batch DB fetch
    all_pairs, pair_metadata = _find_all_candidates_batched(facts_with_embeddings,
                                                            exclude_ids=exclude_ids)

    if not all_pairs:
        return 0

    # Step 2: Single LLM call for all pairs
    llm_start = time.time()
    results = _check_contradictions_batch_pairs(all_pairs)
    print(f"       [conflict] LLM check ({len(all_pairs)} pairs): {time.time() - llm_start:.1f}s")

    # Step 3: Process results and resolve conflicts
    total_conflicts = 0
    for result in results:
        idx = result.get("pair_index")
        if idx is None or idx < 0 or idx >= len(pair_metadata):
            continue
        if not result.get("is_contradiction", False):
            continue

        meta = pair_metadata[idx]
        reasoning = result.get("reasoning", "N/A")

        if interactive:
            resolution = _ask_user_resolution(meta["old_fact_text"], meta["new_fact_text"], reasoning)
        else:
            resolution = "keep_new"

        if resolution == "keep_new":
            conflict = Conflict(
                old_fact_id=meta["old_fact_id"],
                new_fact_id=meta["new_fact_id"],
                resolution="recency_wins",
            )
            db.insert_conflict(conflict)
            db.deactivate_fact(meta["old_fact_id"], superseded_on=current_date)
            total_conflicts += 1
            print(f"       CONFLICT RESOLVED: '{meta['old_fact_text']}' → superseded by '{meta['new_fact_text']}'")

        elif resolution == "keep_old":
            conflict = Conflict(
                old_fact_id=meta["old_fact_id"],
                new_fact_id=meta["new_fact_id"],
                resolution="keep_old",
            )
            db.insert_conflict(conflict)
            db.deactivate_fact(meta["new_fact_id"], superseded_on=current_date)
            total_conflicts += 1
            print(f"       CONFLICT RESOLVED: Kept '{meta['old_fact_text']}', discarded new")

        elif resolution == "keep_both":
            conflict = Conflict(
                old_fact_id=meta["old_fact_id"],
                new_fact_id=meta["new_fact_id"],
                resolution="keep_both",
            )
            db.insert_conflict(conflict)
            print(f"       CONFLICT LOGGED: Both facts kept active")

        else:
            # Custom corrected fact
            db.deactivate_fact(meta["old_fact_id"], superseded_on=current_date)
            db.deactivate_fact(meta["new_fact_id"], superseded_on=current_date)
            corrected_fact = AtomicFact(memcell_id=None, fact_text=resolution,
                                        conversation_date=current_date)
            corrected_id = db.insert_atomic_fact(corrected_fact)
            corrected_embedding = embed_text(resolution)
            vector_store.upsert_fact(corrected_id, None, corrected_embedding)
            conflict = Conflict(
                old_fact_id=meta["old_fact_id"],
                new_fact_id=meta["new_fact_id"],
                resolution=f"corrected:{corrected_id}",
            )
            db.insert_conflict(conflict)
            total_conflicts += 1
            print(f"       CONFLICT RESOLVED: Both replaced with '{resolution}'")

    return total_conflicts
