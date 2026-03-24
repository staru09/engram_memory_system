import json
import os
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
import db
import vector_store
from agentic_layer.vectorize_service import embed_text

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "memory_update.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def _find_similar_facts(embedding: list[float], category: str = None, top_k: int = 10) -> list[dict]:
    """Find similar existing facts, optionally scoped to same category."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    category_filter = None
    if category:
        category_filter = [category]

    results = vector_store.search_facts(embedding, top_k=top_k, category_filter=category_filter)

    # Only return active facts
    if not results:
        return []

    fact_ids = [r["fact_id"] for r in results]
    active_ids = db.filter_active_fact_ids(fact_ids)

    return [r for r in results if r["fact_id"] in active_ids]


def decide_operation(new_fact: str, existing_facts: list[dict]) -> dict:
    """Ask LLM to decide ADD/UPDATE/DELETE/NOOP for a new fact."""
    if not existing_facts:
        return {"operation": "ADD", "target_id": None, "reasoning": "No existing facts in this category"}

    prompt_template = _load_prompt()

    # Format existing facts with IDs
    existing_text = "\n".join(
        f"[ID={f['fact_id']}] {f.get('fact_text', '')}"
        for f in existing_facts
    )

    prompt = (prompt_template
              .replace("{new_fact}", new_fact)
              .replace("{existing_facts}", existing_text))

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        result = json.loads(text)

        # Validate operation
        op = result.get("operation", "ADD").upper()
        if op not in ("ADD", "UPDATE", "DELETE", "NOOP"):
            op = "ADD"

        return {
            "operation": op,
            "target_id": result.get("target_id"),
            "reasoning": result.get("reasoning", ""),
        }
    except Exception as e:
        print(f"    [memory-update] LLM decision failed: {e}, defaulting to ADD")
        return {"operation": "ADD", "target_id": None, "reasoning": f"LLM error: {e}"}


def execute_memory_update(new_fact_text: str, category: str, embedding: list[float],
                          memcell_id: int, current_date: str) -> dict:
    """Full memory update cycle: find similar → decide → execute.

    Returns dict with operation result and stats.
    """
    # 1. Find similar existing facts (category-scoped)
    similar = _find_similar_facts(embedding, category=category, top_k=10)

    # 2. LLM decides operation
    decision = decide_operation(new_fact_text, similar)
    operation = decision["operation"]
    target_id = decision.get("target_id")

    print(f"    [memory-update] {operation}: \"{new_fact_text[:50]}...\" "
          f"{'→ target ' + str(target_id) if target_id else ''} "
          f"({decision.get('reasoning', '')[:50]})")

    # 3. Execute operation
    result = {
        "operation": operation,
        "fact_id": None,
        "target_id": target_id,
        "reasoning": decision.get("reasoning", ""),
    }

    if operation == "ADD":
        from models import AtomicFact
        fact = AtomicFact(
            memcell_id=memcell_id,
            fact_text=new_fact_text,
            conversation_date=current_date,
            category=category,
        )
        fact_id = db.insert_atomic_fact(fact)
        vector_store.upsert_fact(fact_id, memcell_id, embedding,
                                 current_date, new_fact_text, category)
        result["fact_id"] = fact_id

    elif operation == "UPDATE" and target_id:
        # Soft update: deactivate old, insert new, link
        db.deactivate_fact(target_id, superseded_on=current_date)

        from models import AtomicFact
        fact = AtomicFact(
            memcell_id=memcell_id,
            fact_text=new_fact_text,
            conversation_date=current_date,
            category=category,
        )
        new_fact_id = db.insert_atomic_fact(fact)
        vector_store.upsert_fact(new_fact_id, memcell_id, embedding,
                                  current_date, new_fact_text, category)
        db.insert_fact_update(old_fact_id=target_id, new_fact_id=new_fact_id,
                              update_type="change")
        result["fact_id"] = new_fact_id

    elif operation == "DELETE" and target_id:
        db.deactivate_fact(target_id, superseded_on=current_date)
        db.insert_fact_update(old_fact_id=target_id, new_fact_id=None,
                              update_type="cancellation")

    elif operation == "NOOP":
        pass  # nothing to do

    else:
        # Fallback: if UPDATE/DELETE without target_id, treat as ADD
        if operation in ("UPDATE", "DELETE") and not target_id:
            print(f"    [memory-update] {operation} without target_id, falling back to ADD")
            from models import AtomicFact
            fact = AtomicFact(
                memcell_id=memcell_id,
                fact_text=new_fact_text,
                conversation_date=current_date,
                category=category,
            )
            fact_id = db.insert_atomic_fact(fact)
            vector_store.upsert_fact(fact_id, memcell_id, embedding,
                                     current_date, new_fact_text, category)
            result["fact_id"] = fact_id
            result["operation"] = "ADD"

    return result


def update_memories_batch(facts_with_embeddings: list[dict], memcell_id: int,
                          current_date: str) -> dict:
    """Process a batch of facts through the memory update pipeline.

    Args:
        facts_with_embeddings: list of {"text": str, "category": str, "embedding": list[float]}
        memcell_id: parent memcell ID
        current_date: IST date string

    Returns:
        {"added": int, "updated": int, "deleted": int, "noops": int, "results": list}
    """
    stats = {"added": 0, "updated": 0, "deleted": 0, "noops": 0, "results": []}

    for fact_entry in facts_with_embeddings:
        result = execute_memory_update(
            new_fact_text=fact_entry["text"],
            category=fact_entry.get("category"),
            embedding=fact_entry["embedding"],
            memcell_id=memcell_id,
            current_date=current_date,
        )
        stats["results"].append(result)

        match result["operation"]:
            case "ADD":
                stats["added"] += 1
            case "UPDATE":
                stats["updated"] += 1
            case "DELETE":
                stats["deleted"] += 1
            case "NOOP":
                stats["noops"] += 1

    return stats
