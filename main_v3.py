"""
V3 Hybrid Ingestion Pipeline:
  - Segmented extraction (from v1) with V3 prompt enhancements
  - ADD/UPDATE/DELETE/NOOP per-fact memory operations (from v2)
  - Graph upsert (from v2)
  - Rolling conversation summaries (new) — fed into extraction as context
  - Incremental profile update (from v2)

Combines the best of both pipelines:
  v1: segmentation + parallel extraction → better fact quality
  v2: per-fact memory ops + graph + incremental profile → cleaner DB, faster profile
  mem0: conversation summary as extraction context + richness guard on UPDATE
"""

import json
import os
import time
import asyncio
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import GEMINI_API_KEY, GEMINI_MODEL
from google import genai
import db
import vector_store
from models import MemCell, AtomicFact, Foresight
from memory_layer.memcell_extractor import extract_segments
from memory_layer.episode_extractor import extract_episode
from memory_layer.cluster_manager import assign_to_scene
from memory_layer.memory_updater import update_memories_batch
from agentic_layer.vectorize_service import embed_text, embed_texts

client = genai.Client(api_key=GEMINI_API_KEY)

EXTRACTION_BATCH_SIZE = 10
STORAGE_CONCURRENCY = 10

# No global episode accumulation needed — conversation summary replaces it


# ═══════════════════════════════════════════════════════════════════
# Conversation Summary
# ═══════════════════════════════════════════════════════════════════

def _get_conversation_summary() -> str:
    """Fetch the current rolling conversation summary from DB."""
    try:
        row = db.get_conversation_summary()
        return row if row else ""
    except Exception:
        return ""


def _update_conversation_summary_async(new_episodes: list[str], current_date: str):
    """Update rolling conversation summary in background (1 LLM call)."""
    existing_summary = _get_conversation_summary()

    new_context = "\n".join(f"- {ep}" for ep in new_episodes)

    prompt = f"""You are maintaining a rolling summary of a long-term conversation between two people.

EXISTING SUMMARY:
{existing_summary if existing_summary else "(No prior summary — this is the first batch.)"}

NEW EPISODES (just extracted):
{new_context}

Current date: {current_date}

Update the summary to incorporate the new episodes. Keep it concise (10-15 sentences max).
Focus on: key facts about each person, their relationship, ongoing plans, recent events, and any changes.
Drop details that are no longer relevant (cancelled plans, resolved issues).

Return ONLY the updated summary text, no JSON or formatting."""

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        summary = response.text.strip()
        db.upsert_conversation_summary(summary)
        print(f"  [summary] Updated ({len(summary)} chars)")
    except Exception as e:
        print(f"  [summary] Update failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# Extraction (segmented, with V3 enhancements)
# ═══════════════════════════════════════════════════════════════════

def _extract_segment(seg: dict, current_date: str, conversation_summary: str = None) -> dict:
    """Extract episode, facts, foresight, and scene hint for one segment."""
    result = extract_episode(seg["dialogue"], current_date,
                             conversation_summary=conversation_summary)
    return {
        "segment": seg,
        "episode_text": result["episode"],
        "atomic_facts": result["atomic_facts"],
        "foresight": result.get("foresight", []),
        "relationships": result.get("relationships", []),
        "scene_hint": result.get("scene_hint"),
    }


# ═══════════════════════════════════════════════════════════════════
# Storage (Phase 1: memcell + scene, Phase 2: ADD/UPDATE/DELETE/NOOP)
# ═══════════════════════════════════════════════════════════════════

def _store_memcell_and_scene(ext: dict, source_id: str, episode_embedding: list[float],
                              current_date: str) -> dict:
    """Store memcell and assign to scene. Returns memcell_id and scene_id."""
    seg = ext["segment"]
    episode_text = ext["episode_text"]

    # Insert memcell
    cell = MemCell(
        episode_text=episode_text,
        raw_dialogue=seg.get("dialogue", ""),
        source_id=source_id,
        conversation_date=current_date,
    )
    memcell_id = db.insert_memcell(cell)
    db.update_memcell_embedding(memcell_id, episode_embedding)

    # Assign to scene
    scene_hint = ext.get("scene_hint") or {}
    scene_id = assign_to_scene(
        memcell_id=memcell_id,
        episode_text=episode_text,
        episode_embedding=episode_embedding,
        scene_hint=scene_hint,
    )

    return {"memcell_id": memcell_id, "scene_id": scene_id}


def _process_facts_with_memory_ops(ext: dict, memcell_id: int, current_date: str) -> dict:
    """Embed facts and run ADD/UPDATE/DELETE/NOOP for each fact."""
    raw_facts = ext["atomic_facts"]

    # Normalize facts
    fact_entries = []
    for f in raw_facts:
        if isinstance(f, dict):
            fact_entries.append({"text": f.get("text", ""), "category": f.get("category")})
        else:
            fact_entries.append({"text": str(f), "category": None})

    if not fact_entries:
        return {"added": 0, "updated": 0, "deleted": 0, "noops": 0, "results": []}

    # Batch embed
    fact_texts = [f["text"] for f in fact_entries]
    embeddings = embed_texts(fact_texts)
    for entry, emb in zip(fact_entries, embeddings):
        entry["embedding"] = emb

    # Per-fact ADD/UPDATE/DELETE/NOOP
    stats = update_memories_batch(fact_entries, memcell_id, current_date)
    return stats


def _store_foresight(ext: dict, memcell_id: int):
    """Store foresight signals with embeddings."""
    foresight_signals = ext.get("foresight", [])
    if not foresight_signals:
        return 0

    foresight_texts = [fs.get("description", "") for fs in foresight_signals]
    foresight_embeddings = embed_texts(foresight_texts) if foresight_texts else []

    count = 0
    for fs, emb in zip(foresight_signals, foresight_embeddings):
        valid_from = _parse_dt(fs.get("valid_from"))
        valid_until = _parse_dt(fs.get("valid_until"))

        foresight = Foresight(
            memcell_id=memcell_id,
            description=fs.get("description", ""),
            valid_from=valid_from,
            valid_until=valid_until,
        )
        foresight_id = db.insert_foresight(foresight)
        if emb:
            db.update_foresight_embedding(foresight_id, emb)
        count += 1

    return count


def _store_graph_relationships(ext: dict, current_date: str):
    """Extract and store graph relationships from the extraction result."""
    # The V3 narrative_synthesis prompt doesn't extract entities/relationships
    # directly, but we can derive them from the episode text if graph_store exists
    try:
        import graph_store
        # For now, graph relationships come from the extraction if available
        # V3 prompt focuses on atomic facts; graph extraction is secondary
        relationships = ext.get("relationships", [])
        for rel in relationships:
            graph_store.upsert_relationship(
                source=rel.get("source", ""),
                source_type=rel.get("source_type", ""),
                relation=rel.get("relation", ""),
                target=rel.get("target", ""),
                target_type=rel.get("target_type", ""),
                conversation_date=current_date,
            )
        return len(relationships)
    except ImportError:
        return 0
    except Exception as e:
        print(f"  [graph] Upsert failed: {e}")
        return 0


def _parse_dt(val):
    """Parse datetime from LLM output."""
    if not val or val == "null" or val == "None":
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt)
        except ValueError:
            continue
    return None


# ═══════════════════════════════════════════════════════════════════
# Incremental Profile Update (no LLM call)
# ═══════════════════════════════════════════════════════════════════

def _incremental_profile_update(all_stats: list[dict]):
    """Update user profile incrementally from memory operations. No LLM call."""
    profile = db.get_user_profile()
    if not profile:
        return

    current_facts = list(profile.explicit_facts) if profile.explicit_facts else []
    changed = False

    for stats in all_stats:
        for result in stats.get("results", []):
            op = result.get("operation")

            if op == "ADD" and result.get("fact_id"):
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


# ═══════════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════════

def ingest_v3(conversation: list[dict], source_id: str = "default",
              current_date: str = None, extract_all_speakers: bool = False):
    """
    V3 Hybrid Ingestion Pipeline:
    1. Segmentation (1 LLM call)
    2. Parallel extraction with V3 prompt + episode context (N LLM calls)
    3. Batch embed + store memcells/scenes
    4. Per-fact ADD/UPDATE/DELETE/NOOP (K LLM tool calls)
    5. Foresight upsert
    6. Graph upsert (if available)
    7. Incremental profile update (no LLM)
    8. Conversation summary update (1 LLM call, async)
    """
    IST = timezone(timedelta(hours=5, minutes=30))
    if current_date is None:
        current_date = datetime.now(IST).strftime("%Y-%m-%d")

    pipeline_start = time.time()

    print(f"[v3] [segment] Segmenting {len(conversation)} turns...")
    t0 = time.time()
    segments = extract_segments(conversation, extract_all_speakers=extract_all_speakers)
    print(f"  {len(segments)} segments ({time.time() - t0:.1f}s)")

    conv_summary = _get_conversation_summary()

    all_stats = []
    all_new_episodes = []
    total_facts = {"added": 0, "updated": 0, "deleted": 0, "noops": 0}
    total_foresight = 0
    total_graph = 0

    total_batches = (len(segments) + EXTRACTION_BATCH_SIZE - 1) // EXTRACTION_BATCH_SIZE

    for batch_start in range(0, len(segments), EXTRACTION_BATCH_SIZE):
        batch_end = min(batch_start + EXTRACTION_BATCH_SIZE, len(segments))
        batch = segments[batch_start:batch_end]
        batch_num = (batch_start // EXTRACTION_BATCH_SIZE) + 1

        print(f"\n[v3] [extract] Batch {batch_num}/{total_batches}: {len(batch)} segments...")
        t0 = time.time()
        batch_extractions = [None] * len(batch)

        with ThreadPoolExecutor(max_workers=EXTRACTION_BATCH_SIZE) as executor:
            future_to_idx = {
                executor.submit(_extract_segment, seg, current_date, conv_summary): i
                for i, seg in enumerate(batch)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                batch_extractions[idx] = future.result()
                seg_info = batch_extractions[idx]
                print(f"    {seg_info['segment']['topic_hint']} ({len(seg_info['atomic_facts'])} facts)")

        for ext in batch_extractions:
            all_new_episodes.append(ext["episode_text"])
        print(f"  {time.time() - t0:.1f}s")

        print(f"[v3] [store] Embedding + storing memcells...")
        t0 = time.time()
        episode_texts = [ext["episode_text"] for ext in batch_extractions]
        episode_embeddings = embed_texts(episode_texts)

        memcell_data = []
        for ext, emb in zip(batch_extractions, episode_embeddings):
            mc = _store_memcell_and_scene(ext, source_id, emb, current_date)
            memcell_data.append(mc)
        print(f"  {len(memcell_data)} memcells ({time.time() - t0:.1f}s)")

        print(f"[v3] [memory-ops] ADD/UPDATE/DELETE/NOOP...")
        t0 = time.time()
        for ext, mc in zip(batch_extractions, memcell_data):
            stats = _process_facts_with_memory_ops(ext, mc["memcell_id"], current_date)
            all_stats.append(stats)
            total_facts["added"] += stats["added"]
            total_facts["updated"] += stats["updated"]
            total_facts["deleted"] += stats["deleted"]
            total_facts["noops"] += stats["noops"]
        print(f"  ADD={total_facts['added']} UPD={total_facts['updated']} "
              f"DEL={total_facts['deleted']} NOOP={total_facts['noops']} ({time.time() - t0:.1f}s)")

        t0 = time.time()
        for ext, mc in zip(batch_extractions, memcell_data):
            total_foresight += _store_foresight(ext, mc["memcell_id"])
        for ext in batch_extractions:
            total_graph += _store_graph_relationships(ext, current_date)
        if total_foresight or total_graph:
            print(f"[v3] [extras] {total_foresight} foresight, {total_graph} graph ({time.time() - t0:.1f}s)")

    print(f"\n[v3] [profile] Incremental update (no LLM)...")
    t0 = time.time()
    _incremental_profile_update(all_stats)
    print(f"  Done ({time.time() - t0:.1f}s)")

    print(f"[v3] [summary] Updating conversation summary...")
    t0 = time.time()
    _update_conversation_summary_async(all_new_episodes, current_date)

    # No episode persistence needed — conversation summary handles cross-session context

    # Invalidate caches
    try:
        from agentic_layer.retrieval_utils import invalidate_foresight_cache
        invalidate_foresight_cache()
    except ImportError:
        pass

    total_time = time.time() - pipeline_start
    print(f"\n[v3] Pipeline complete in {total_time:.1f}s")
    print(f"  Segments: {len(segments)}")
    print(f"  Facts: ADD={total_facts['added']} UPDATE={total_facts['updated']} "
          f"DELETE={total_facts['deleted']} NOOP={total_facts['noops']}")
    print(f"  Foresight: {total_foresight}")
    print(f"  Graph relationships: {total_graph}")

    return {
        "segments": len(segments),
        **total_facts,
        "foresight": total_foresight,
        "graph": total_graph,
        "time": total_time,
    }


def reset_v3():
    """Reset global state for clean re-ingestion."""
    pass  # No global state to reset — conversation summary is in DB, cleared by DB reset
