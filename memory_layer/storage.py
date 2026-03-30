import asyncio
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import db
import vector_store
from models import MemCell, AtomicFact, Foresight
from memory_layer.profile_manager import detect_conflicts_batch
from agentic_layer.vectorize_service import embed_texts

STORAGE_CONCURRENCY = 10
_executor = ThreadPoolExecutor(max_workers=STORAGE_CONCURRENCY * 2)


async def _store_segment_data(ext: dict, source_id: str, episode_embedding: list[float],
                              semaphore: asyncio.Semaphore,
                              loop: asyncio.AbstractEventLoop,
                              current_date: str = None) -> dict:
    """Phase 1: Store memcell, facts, vectors, foresight, scene (NO conflict detection)."""
    async with semaphore:
        seg = ext["segment"]
        episode_text = ext["episode_text"]
        atomic_facts = ext["atomic_facts"]
        foresight_signals = ext["foresight"]

        seg_label = f"Segment {seg['segment_id']}: {seg['topic_hint']}"
        print(f"       [phase1] Starting {seg_label}")

        # Store MemCell
        cell = MemCell(episode_text=episode_text, raw_dialogue=seg["dialogue"],
                       source_id=source_id, conversation_date=current_date)
        memcell_id = await loop.run_in_executor(_executor, db.insert_memcell, cell)

        # Parse fact items
        parsed_facts = []
        for fact_item in atomic_facts:
            if isinstance(fact_item, dict):
                parsed_facts.append({
                    "text": fact_item.get("text", ""),
                    "category": fact_item.get("category", "general"),
                })
            else:
                parsed_facts.append({"text": fact_item, "category": "general"})

        fact_texts = [f["text"] for f in parsed_facts]

        # Batch-embed all facts
        if fact_texts:
            embeddings = await loop.run_in_executor(_executor, embed_texts, fact_texts)
        else:
            embeddings = []

        # Insert facts into PostgreSQL
        fact_ids = []
        for pf in parsed_facts:
            fact = AtomicFact(memcell_id=memcell_id, fact_text=pf["text"],
                              category_name=pf["category"], conversation_date=current_date)
            fact_id = await loop.run_in_executor(_executor, db.insert_atomic_fact, fact)
            fact_ids.append(fact_id)

        # Upsert vectors into Qdrant
        for fact_id, pf, embedding in zip(fact_ids, parsed_facts, embeddings):
            await loop.run_in_executor(
                _executor, vector_store.upsert_fact, fact_id, memcell_id, embedding,
                current_date, pf["category"]
            )

        # Store episode embedding
        await loop.run_in_executor(_executor, db.update_memcell_embedding, memcell_id, episode_embedding)

        # Batch-embed foresight descriptions
        foresight_texts = [fs["description"] for fs in foresight_signals]
        foresight_embeddings = (
            await loop.run_in_executor(_executor, embed_texts, foresight_texts)
            if foresight_texts else []
        )

        # Store foresight signals with embeddings
        for i, fs in enumerate(foresight_signals):
            valid_from = None
            valid_until = None
            def _parse_foresight_dt(val):
                if not val or val == "null" or val == "None":
                    return None
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(str(val).strip(), fmt)
                    except ValueError:
                        continue
                return None

            try:
                valid_from = _parse_foresight_dt(fs.get("valid_from"))
                valid_until = _parse_foresight_dt(fs.get("valid_until"))
            except (ValueError, TypeError) as e:
                print(f"       Warning: Could not parse foresight dates: {e}")

            f = Foresight(
                memcell_id=memcell_id, description=fs["description"],
                valid_from=valid_from, valid_until=valid_until,
            )
            foresight_id = await loop.run_in_executor(_executor, db.insert_foresight, f)
            if i < len(foresight_embeddings):
                await loop.run_in_executor(_executor, db.update_foresight_embedding,
                                           foresight_id, foresight_embeddings[i])

        print(f"   [phase1] Done {seg_label} → {len(atomic_facts)} facts")

        return {
            "memcell_id": memcell_id,
            "seg_label": seg_label,
            "fact_ids": fact_ids,
            "atomic_facts": atomic_facts,
            "parsed_facts": parsed_facts,
            "embeddings": embeddings,
        }


async def store_batch(batch_extractions: list[dict], episode_embeddings: list[list[float]],
                      source_id: str, interactive: bool,
                      current_date: str = None) -> list[dict]:
    """Two-phase storage: concurrent data insertion, then sequential conflict detection."""
    loop = asyncio.get_event_loop()
    semaphore = asyncio.Semaphore(STORAGE_CONCURRENCY)

    # Phase 1 — Concurrent: store all segment data in parallel
    phase1_start = time.time()
    phase1_tasks = [
        _store_segment_data(ext, source_id, emb, semaphore, loop, current_date=current_date)
        for ext, emb in zip(batch_extractions, episode_embeddings)
    ]
    phase1_results = await asyncio.gather(*phase1_tasks)
    print(f"\n       [Phase 1] Complete: {time.time() - phase1_start:.1f}s")

    # Phase 2 — Single conflict detection call for all facts across all segments
    # Collect all facts + embeddings and all batch fact IDs
    all_batch_fact_ids = set()
    all_facts_with_embeddings = []
    for p1 in phase1_results:
        all_batch_fact_ids.update(p1.get("fact_ids", []))
        fact_texts = [pf["text"] for pf in p1["parsed_facts"]]
        for fid, ft, emb in zip(p1["fact_ids"], fact_texts, p1["embeddings"]):
            all_facts_with_embeddings.append({"fact_id": fid, "fact_text": ft, "embedding": emb})

    phase2_start = time.time()
    print(f"       [Phase 2] Running conflict detection ({len(all_facts_with_embeddings)} facts, batch-combined)...")
    total_conflicts = await loop.run_in_executor(
        _executor, detect_conflicts_batch, all_facts_with_embeddings, interactive,
        current_date, all_batch_fact_ids
    )
    print(f"       [Phase 2] Complete: {total_conflicts} conflicts in {time.time() - phase2_start:.1f}s")

    total_storage = time.time() - phase1_start
    print(f"       Total storage: {total_storage:.1f}s")

    # Build final results
    results = []
    for i, p1 in enumerate(phase1_results):
        results.append({
            "memcell_id": p1["memcell_id"],
            "facts": len(p1["atomic_facts"]),
            "conflicts": total_conflicts if i == 0 else 0,
            "parsed_facts": p1.get("parsed_facts", []),
            "fact_ids": p1.get("fact_ids", []),
        })

    return results
