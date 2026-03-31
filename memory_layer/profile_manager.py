import json
import os
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
from models import Conflict, AtomicFact
import db
import vector_store
from agentic_layer.vectorize_service import embed_text

client = genai.Client(api_key=GEMINI_API_KEY)

_BATCH_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "conflict_detection_batch.txt")

CONFLICT_SIMILARITY_THRESHOLD = 0.85
CONFLICT_BATCH_SIZE = 25


def _load_prompt_file(path):
    with open(path) as f:
        return f.read()


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


def _find_all_candidates_batched(facts_with_embeddings: list[dict],
                                 exclude_ids: set = None) -> tuple[list[dict], list[dict]]:
    """
    Find conflict candidates for ALL facts with minimal DB round-trips.
    Excludes facts in exclude_ids (current batch) from candidate search at the query level.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_pairs = []
    pair_metadata = []

    if exclude_ids is None:
        exclude_ids = set()

    search_start = time.time()

    def _search_one_fact(new_fact):
        """Run vector + keyword search for one fact (same category only)."""
        category = new_fact.get("category")

        similar = vector_store.search_facts(new_fact["embedding"], top_k=3,
                                            exclude_ids=exclude_ids,
                                            category_name=category)
        vector_cands = [
            hit for hit in similar
            if hit["score"] >= CONFLICT_SIMILARITY_THRESHOLD
        ]
        seen = {hit["fact_id"] for hit in vector_cands}

        keyword_hits = db.keyword_search_facts(new_fact["fact_text"], top_k=3,
                                               exclude_ids=exclude_ids)
        keyword_cands = [
            {"fact_id": row["id"], "memcell_id": row["memcell_id"], "score": float(row["rank"])}
            for row in keyword_hits
            if row["id"] not in seen and (not category or row.get("category_name") == category)
        ]

        return {"new_fact": new_fact, "candidates": vector_cands + keyword_cands}

    with ThreadPoolExecutor(max_workers=min(len(facts_with_embeddings), 10)) as executor:
        futures = {executor.submit(_search_one_fact, f): i for i, f in enumerate(facts_with_embeddings)}
        search_results = [None] * len(facts_with_embeddings)
        for future in as_completed(futures):
            idx = futures[future]
            search_results[idx] = future.result()

    print(f"       [conflict] Candidate search: {time.time() - search_start:.1f}s")

    all_candidate_ids = set()
    for sr in search_results:
        for cand in sr["candidates"]:
            all_candidate_ids.add(cand["fact_id"])

    fetch_start = time.time()
    facts_map = db.get_facts_by_ids(list(all_candidate_ids))
    print(f"       [conflict] Batch fact fetch ({len(all_candidate_ids)} facts): {time.time() - fetch_start:.1f}s")

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

    Returns:
        Total number of conflicts detected.
    """
    import time

    all_pairs, pair_metadata = _find_all_candidates_batched(facts_with_embeddings,
                                                            exclude_ids=exclude_ids)

    if not all_pairs:
        return 0

    llm_start = time.time()
    results = _check_contradictions_batch_pairs(all_pairs)
    print(f"       [conflict] LLM check ({len(all_pairs)} pairs): {time.time() - llm_start:.1f}s")

    total_conflicts = 0
    for result in results:
        idx = result.get("pair_index")
        if idx is None or idx < 0 or idx >= len(pair_metadata):
            continue
        if not result.get("is_contradiction", False):
            continue

        meta = pair_metadata[idx]

        conflict = Conflict(
            old_fact_id=meta["old_fact_id"],
            new_fact_id=meta["new_fact_id"],
            resolution="recency_wins",
        )
        db.insert_conflict(conflict)
        db.deactivate_fact(meta["old_fact_id"], superseded_on=current_date)
        total_conflicts += 1
        print(f"       CONFLICT: '{meta['old_fact_text'][:60]}' → superseded by '{meta['new_fact_text'][:60]}'")

    return total_conflicts
