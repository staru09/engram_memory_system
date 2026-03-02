import json
import os
from datetime import datetime
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
from agentic_layer.fetch_mem_service import retrieve, compose_context
from agentic_layer.retrieval_utils import hybrid_search

client = genai.Client(api_key=GEMINI_API_KEY)

_SUFFICIENCY_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "memory_layer", "prompts", "sufficiency_check.txt"
)
_REWRITE_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "memory_layer", "prompts", "query_rewrite.txt"
)

MAX_RETRIEVAL_ROUNDS = 2


def _load_prompt(path):
    with open(path) as f:
        return f.read()


def _check_sufficiency(query: str, context: str) -> dict:
    """Ask Gemini if the retrieved context is sufficient to answer the query."""
    prompt_template = _load_prompt(_SUFFICIENCY_PROMPT_PATH)
    prompt = prompt_template.replace("{query}", query).replace("{context}", context)

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text)


def _generate_rewrite_queries(original_query: str, key_info: list, missing_info: list) -> list[str]:
    """Generate 2-3 refined queries targeting missing information."""
    prompt_template = _load_prompt(_REWRITE_PROMPT_PATH)
    prompt = (
        prompt_template
        .replace("{original_query}", original_query)
        .replace("{key_info}", "\n".join(f"- {k}" for k in key_info) if key_info else "None")
        .replace("{missing_info}", "\n".join(f"- {m}" for m in missing_info) if missing_info else "None")
    )

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    parsed = json.loads(text)
    return parsed.get("queries", [])


def _merge_retrieval_results(existing: dict, new: dict) -> dict:
    """Merge new retrieval results into existing, deduplicating episodes and facts."""
    seen_ep_ids = {ep["memcell_id"] for ep in existing["episodes"]}
    for ep in new["episodes"]:
        if ep["memcell_id"] not in seen_ep_ids:
            existing["episodes"].append(ep)
            seen_ep_ids.add(ep["memcell_id"])

    seen_fact_ids = {f["fact_id"] for f in existing["facts"]}
    for f in new["facts"]:
        if f["fact_id"] not in seen_fact_ids:
            existing["facts"].append(f)
            seen_fact_ids.add(f["fact_id"])

    # Keep foresight and profile from latest
    if new["foresight"]:
        existing["foresight"] = new["foresight"]
    if new["profile"]:
        existing["profile"] = new["profile"]

    return existing


def agentic_retrieve(query: str, query_time: datetime = None, verbose: bool = True) -> dict:
    """
    Agentic retrieval pipeline:
      1. Run initial MemScene-guided retrieval
      2. Check sufficiency with Gemini
      3. If insufficient: rewrite queries, retrieve again, merge
      4. Return final composed context

    Args:
        query: The user's query
        query_time: Timestamp for foresight filtering
        verbose: Print retrieval trace

    Returns:
        {
            "context": str,          # Composed context text
            "is_sufficient": bool,   # Final sufficiency verdict
            "rounds": int,           # Number of retrieval rounds
            "result": dict,          # Raw retrieval result
        }
    """
    if query_time is None:
        query_time = datetime.now()

    # Round 1: initial retrieval
    if verbose:
        print(f"\n[Retrieval Round 1] Query: '{query}'")

    result = retrieve(query, query_time)
    context = compose_context(result)

    if verbose:
        print(f"  Found {len(result['episodes'])} episodes, {len(result['facts'])} facts, "
              f"{len(result['foresight'])} active foresight")

    if not result["facts"]:
        if verbose:
            print("  No facts found. Returning empty context.")
        return {
            "context": context,
            "is_sufficient": False,
            "rounds": 1,
            "result": result,
        }

    # Sufficiency check
    sufficiency = _check_sufficiency(query, context)
    is_sufficient = sufficiency.get("is_sufficient", False)

    if verbose:
        print(f"  Sufficient: {is_sufficient}")
        if sufficiency.get("reasoning"):
            print(f"  Reasoning: {sufficiency['reasoning']}")

    # Round 2+ if insufficient
    current_round = 1
    while not is_sufficient and current_round < MAX_RETRIEVAL_ROUNDS:
        current_round += 1

        key_info = sufficiency.get("key_information_found", [])
        missing_info = sufficiency.get("missing_information", [])

        if verbose:
            print(f"\n[Retrieval Round {current_round}] Rewriting queries...")
            if missing_info:
                print(f"  Missing: {', '.join(missing_info)}")

        # Generate refined queries
        rewrite_queries = _generate_rewrite_queries(query, key_info, missing_info)

        if verbose:
            for i, rq in enumerate(rewrite_queries, 1):
                print(f"  Rewrite {i}: '{rq}'")

        # Retrieve with each rewritten query and merge
        for rq in rewrite_queries:
            new_result = retrieve(rq, query_time)
            result = _merge_retrieval_results(result, new_result)

        # Recompose context and check sufficiency again
        context = compose_context(result)
        sufficiency = _check_sufficiency(query, context)
        is_sufficient = sufficiency.get("is_sufficient", False)

        if verbose:
            print(f"  After merge: {len(result['episodes'])} episodes, {len(result['facts'])} facts")
            print(f"  Sufficient: {is_sufficient}")

    return {
        "context": context,
        "is_sufficient": is_sufficient,
        "rounds": current_round,
        "result": result,
    }
