"""Cumulative scale test: ingest stages incrementally and eval at each checkpoint.

Flow:
  Reset DB → Ingest Stage 1 (100 msgs) → Eval Stage 1
           → Ingest Stage 2 (+100 = 200 msgs) → Eval Stages 1,2
           → Ingest Stage 3 (+100 = 300 msgs) → Eval Stages 1,2,3
           → Ingest Stage 4 (+200 = 500 msgs) → Eval Stages 1,2,3,4

Usage:
  python test/cumulative_scale_test.py
  python test/cumulative_scale_test.py --skip-to 3      # Resume from stage 3 (no reset)
"""

import json
import sys
import os
import time
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import vector_store
from test.eval_queries import run_evaluation, STAGE_DEFAULTS

SCALE_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scale_data")

STAGES = [
    {"file": "stage_1_messages.json", "stage": 1, "cumulative_msgs": 100},
    {"file": "stage_2_messages.json", "stage": 2, "cumulative_msgs": 200},
    {"file": "stage_3_messages.json", "stage": 3, "cumulative_msgs": 300},
    {"file": "stage_4_messages.json", "stage": 4, "cumulative_msgs": 500},
]


def reset_databases():
    """Wipe and recreate all tables and Qdrant collections."""
    print("Resetting databases...")
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS conflicts CASCADE;
        DROP TABLE IF EXISTS foresight CASCADE;
        DROP TABLE IF EXISTS atomic_facts CASCADE;
        DROP TABLE IF EXISTS memcells CASCADE;
        DROP TABLE IF EXISTS memscenes CASCADE;
        DROP TABLE IF EXISTS user_profile CASCADE;
    """)
    conn.commit()
    cur.close()
    conn.close()

    from qdrant_client import QdrantClient
    from config import QDRANT_HOST, QDRANT_PORT
    qclient = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False, timeout=30)
    for name in ("facts", "scenes"):
        if qclient.collection_exists(name):
            qclient.delete_collection(name)

    db.init_schema()
    vector_store.init_collections()
    print("Databases reset.\n")


def main():
    args = sys.argv[1:]

    skip_to = 1
    if "--skip-to" in args:
        idx = args.index("--skip-to") + 1
        if idx < len(args):
            skip_to = int(args[idx])

    from main import ingest_conversation

    # Reset only if starting from stage 1
    if skip_to <= 1:
        reset_databases()
    else:
        print(f"Skipping to stage {skip_to} (no reset)...\n")
        db.init_schema()
        vector_store.init_collections()

    all_metrics = []
    total_start = time.time()

    for stage_info in STAGES:
        stage_num = stage_info["stage"]

        if stage_num < skip_to:
            continue

        filepath = os.path.join(SCALE_DATA_DIR, stage_info["file"])
        if not os.path.exists(filepath):
            print(f"Error: {filepath} not found. Skipping.")
            continue

        print(f"\n{'#'*60}")
        print(f"# STAGE {stage_num}: Ingesting {stage_info['file']}")
        print(f"# Cumulative messages: {stage_info['cumulative_msgs']}")
        print(f"{'#'*60}\n")

        # Load and ingest
        with open(filepath) as f:
            data = json.load(f)

        conversation = data.get("conversation", data)
        source_id = data.get("source_id", f"stage_{stage_num}")
        current_date = data.get("date")

        ingest_start = time.time()
        ingest_conversation(conversation, source_id, current_date=current_date, interactive=False)
        ingest_time = time.time() - ingest_start

        # DB stats after ingestion
        stats = db.get_system_stats()

        print(f"\n{'='*60}")
        print(f"  DB STATE AFTER STAGE {stage_num} ({stage_info['cumulative_msgs']} msgs cumulative)")
        print(f"{'='*60}")
        print(f"  MemCells:    {stats['total_memcells']}")
        print(f"  MemScenes:   {stats['total_scenes']}")
        print(f"  Active Facts: {stats['active_facts']}")
        print(f"  Total Facts:  {stats['total_facts']}")
        print(f"  Conflicts:    {stats['total_conflicts']}")
        dedup = ((stats['total_facts'] - stats['active_facts']) / stats['total_facts'] * 100) if stats['total_facts'] > 0 else 0
        print(f"  Dedup Rate:   {dedup:.1f}%")
        print(f"  Ingest Time:  {ingest_time:.1f}s")

        # Run eval for the current stage
        print(f"\n{'='*60}")
        print(f"  EVALUATING STAGE {stage_num} QUERIES")
        print(f"{'='*60}\n")

        query_time = datetime.strptime(STAGE_DEFAULTS[stage_num], "%Y-%m-%d")
        tag = f"cumulative_s{stage_num}_{stage_info['cumulative_msgs']}msgs"
        run_evaluation(stage_num, query_time, tag=tag)

        all_metrics.append({
            "stage": stage_num,
            "cumulative_msgs": stage_info["cumulative_msgs"],
            "ingest_time": ingest_time,
            **stats,
        })

    # Final summary
    total_time = time.time() - total_start
    print(f"\n\n{'='*60}")
    print(f"  CUMULATIVE SCALE TEST SUMMARY")
    print(f"{'='*60}\n")
    print(f"{'Stage':<8} | {'Msgs':<8} | {'MemCells':<10} | {'Scenes':<8} | {'Facts':<12} | {'Conflicts':<10} | {'Dedup %':<8} | {'Ingest Time':<12}")
    print("-" * 95)
    for m in all_metrics:
        active = m['active_facts']
        total = m['total_facts']
        dedup = ((total - active) / total * 100) if total > 0 else 0
        print(f"{m['stage']:<8} | {m['cumulative_msgs']:<8} | {m['total_memcells']:<10} | {m['total_scenes']:<8} | {active}/{total:<7} | {m['total_conflicts']:<10} | {dedup:<7.1f}% | {m['ingest_time']:<.1f}s")

    print(f"\nTotal time: {total_time:.1f}s")
    print(f"Results saved to results/eval_cumulative_*.md")


if __name__ == "__main__":
    main()
