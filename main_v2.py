import json
import os
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
from agentic_layer.vectorize_service import embed_texts
from memory_layer.memory_updater import update_memories_batch
import db

client = genai.Client(api_key=GEMINI_API_KEY)

_EXTRACTION_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "memory_layer", "prompts", "extraction_v2.txt"
)


def _load_prompt():
    with open(_EXTRACTION_PROMPT_PATH) as f:
        return f.read()


def _format_messages(messages: list[dict]) -> str:
    """Format messages for the extraction prompt."""
    lines = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        date = msg.get("created_at", "")
        lines.append(f"[{date}] {role}: {content}")
    return "\n".join(lines)


def _parse_foresight_dt(val):
    """Parse foresight datetime from LLM output (IST). Stored as-is."""
    if not val or val == "null" or val == "None":
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt)
        except ValueError:
            continue
    return None


def ingest_v2(conversation: list[dict], source_id: str = "default",
              current_date: str = None, extract_all_speakers: bool = False):
    """
    V2 ingestion pipeline:
    1. Single extraction (1 LLM call) — no segmentation
    2. Batch embed facts
    3. Per-fact memory update (ADD/UPDATE/DELETE/NOOP)
    4. Foresight upsert
    5. Incremental profile update
    """
    IST = timezone(timedelta(hours=5, minutes=30))
    if current_date is None:
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    pipeline_start = time.time()

    # ── Step 1: Single extraction (1 LLM call) ──
    print(f"[v2] Extracting from {len(conversation)} messages...")
    t0 = time.time()

    prompt_template = _load_prompt()
    messages_text = _format_messages(conversation)
    prompt = (prompt_template
              .replace("{messages}", messages_text)
              .replace("{current_date}", current_date))

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    try:
        extracted = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[v2] Extraction JSON parse failed: {e}")
        return {"facts_added": 0, "facts_updated": 0, "facts_deleted": 0, "noops": 0}

    raw_facts = extracted.get("atomic_facts", [])
    foresight_signals = extracted.get("foresight", [])
    entities = extracted.get("entities", [])
    relationships = extracted.get("relationships", [])

    # Normalize facts (handle both dict and string format)
    fact_entries = []
    for f in raw_facts:
        if isinstance(f, dict):
            fact_entries.append({"text": f.get("text", ""), "category": f.get("category")})
        else:
            fact_entries.append({"text": str(f), "category": None})

    print(f"[v2] Extracted {len(fact_entries)} facts, {len(foresight_signals)} foresight ({time.time() - t0:.1f}s)")

    if not fact_entries and not foresight_signals:
        print("[v2] Nothing extracted.")
        return {"facts_added": 0, "facts_updated": 0, "facts_deleted": 0, "noops": 0}

    # ── Step 2: Batch embed facts ──
    t0 = time.time()
    fact_texts = [f["text"] for f in fact_entries]
    if fact_texts:
        embeddings = embed_texts(fact_texts)
    else:
        embeddings = []
    print(f"[v2] Embedded {len(fact_texts)} facts ({time.time() - t0:.1f}s)")

    # Attach embeddings to fact entries
    for entry, emb in zip(fact_entries, embeddings):
        entry["embedding"] = emb

    # ── Step 3: We need a memcell_id for fact storage ──
    # In v2, we still create a minimal memcell to maintain DB relationships
    # (atomic_facts.memcell_id FK). No episode text or scene assignment.
    from models import MemCell
    cell = MemCell(
        episode_text=f"[v2 batch] {len(conversation)} messages on {current_date}",
        raw_dialogue="",
        source_id=source_id,
        conversation_date=current_date,
    )
    memcell_id = db.insert_memcell(cell)

    # ── Step 4: Per-fact memory update (ADD/UPDATE/DELETE/NOOP) ──
    t0 = time.time()
    print(f"[v2] Running memory updates ({len(fact_entries)} facts)...")
    stats = update_memories_batch(fact_entries, memcell_id, current_date)
    print(f"[v2] Memory updates: ADD={stats['added']} UPDATE={stats['updated']} "
          f"DELETE={stats['deleted']} NOOP={stats['noops']} ({time.time() - t0:.1f}s)")

    # ── Step 5: Graph upsert ──
    if relationships:
        t0 = time.time()
        try:
            import graph_store
            for rel in relationships:
                graph_store.upsert_relationship(
                    source=rel.get("source", ""),
                    source_type=rel.get("source_type", ""),
                    relation=rel.get("relation", ""),
                    target=rel.get("target", ""),
                    target_type=rel.get("target_type", ""),
                    conversation_date=current_date,
                )
            print(f"[v2] Graph: {len(relationships)} relationships ({time.time() - t0:.1f}s)")
        except Exception as e:
            print(f"[v2] Graph upsert failed (skipping): {e}")

    # ── Step 6: Foresight upsert ──
    if foresight_signals:
        t0 = time.time()
        from models import Foresight
        foresight_texts = [fs.get("description", "") for fs in foresight_signals]
        foresight_embeddings = embed_texts(foresight_texts) if foresight_texts else []

        for fs, emb in zip(foresight_signals, foresight_embeddings):
            foresight = Foresight(
                memcell_id=memcell_id,
                description=fs.get("description", ""),
                valid_from=_parse_foresight_dt(fs.get("valid_from")),
                valid_until=_parse_foresight_dt(fs.get("valid_until")),
            )
            foresight_id = db.insert_foresight(foresight)
            if emb:
                db.update_foresight_embedding(foresight_id, emb)

        print(f"[v2] Foresight: {len(foresight_signals)} signals ({time.time() - t0:.1f}s)")

    # ── Step 6: Incremental profile update ──
    t0 = time.time()
    _incremental_profile_update(stats)
    print(f"[v2] Profile updated ({time.time() - t0:.1f}s)")

    # Invalidate caches
    from agentic_layer.retrieval_utils import invalidate_foresight_cache
    invalidate_foresight_cache()

    total_time = time.time() - pipeline_start
    print(f"[v2] Pipeline complete in {total_time:.1f}s")

    return {
        "facts_added": stats["added"],
        "facts_updated": stats["updated"],
        "facts_deleted": stats["deleted"],
        "noops": stats["noops"],
        "foresight": len(foresight_signals),
        "time": total_time,
    }


def _incremental_profile_update(stats: dict):
    """Update user profile incrementally based on memory operations.
    No LLM call — just add/remove facts from profile."""
    profile = db.get_user_profile()
    if not profile:
        return

    current_facts = list(profile.explicit_facts) if profile.explicit_facts else []
    changed = False

    for result in stats.get("results", []):
        op = result.get("operation")
        fact_text = None

        if op == "ADD" and result.get("fact_id"):
            # Get the fact text from DB
            fact = db.get_fact_by_id(result["fact_id"])
            if fact and fact.get("category") in ("personal_info", "preference", "social"):
                fact_text = fact.get("fact_text", "")
                if fact_text and fact_text not in current_facts:
                    current_facts.append(fact_text)
                    changed = True

        elif op == "DELETE" and result.get("target_id"):
            fact = db.get_fact_by_id(result["target_id"])
            if fact:
                fact_text = fact.get("fact_text", "")
                if fact_text in current_facts:
                    current_facts.remove(fact_text)
                    changed = True

        elif op == "UPDATE" and result.get("target_id") and result.get("fact_id"):
            old_fact = db.get_fact_by_id(result["target_id"])
            new_fact = db.get_fact_by_id(result["fact_id"])
            if old_fact and old_fact.get("fact_text", "") in current_facts:
                current_facts.remove(old_fact["fact_text"])
                changed = True
            if new_fact and new_fact.get("category") in ("personal_info", "preference", "social"):
                current_facts.append(new_fact.get("fact_text", ""))
                changed = True

    if changed:
        db.update_user_profile_facts(current_facts)


# ── DB helper for incremental profile ──
# Add this to db.py if not present
def _ensure_profile_helpers():
    """Check that required DB functions exist."""
    pass
