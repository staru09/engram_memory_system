import json
import sys
import time
import os
import io
import contextlib
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import vector_store
from main import ingest_conversation
from agentic_layer.memory_manager import agentic_retrieve

VERBOSE = False


def reset_databases():
    """Wipe and recreate all tables and Qdrant collections."""
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


def _ingest_quiet(conversation, **kwargs):
    """Run ingestion, suppressing pipeline output unless VERBOSE is set."""
    if VERBOSE:
        return ingest_conversation(conversation, **kwargs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = ingest_conversation(conversation, **kwargs)
    return result


def _get_db_counts():
    """Get quick counts from the database."""
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM memcells")
    memcells = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM atomic_facts WHERE is_active = TRUE")
    facts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM conflicts")
    conflicts = cur.fetchone()[0]
    cur.close()
    conn.close()
    return memcells, facts, conflicts


# ── Test: Dietary Change ──

def test_dietary():
    print()
    print("=" * 60)
    print("  TEST: Dietary Change (Conflict Detection)")
    print("=" * 60)

    # Step 1: Ingest continental cuisine
    print("\n  [Step 1/3] Ingesting: user switches to continental cuisine")
    conv1 = [
        {"role": "user", "content": "I've decided to exclusively eat continental cuisine starting this month. Exploring purely European flavors!"},
        {"role": "assistant", "content": "That's a fun commitment! What motivated the change?"},
        {"role": "user", "content": "Culinary curiosity mostly. I wanted to master French, Italian, and Spanish cooking techniques."},
        {"role": "assistant", "content": "That makes sense. Have you found any good continental recipes yet?"},
        {"role": "user", "content": "Yeah, I've been making a lot of classic ratatouille and authentic pasta carbonara. Actually really enjoying it."},
        {"role": "assistant", "content": "Those sound delicious! European classics are always a hit."},
    ]
    start = time.time()
    _ingest_quiet(conv1, source_id="test_dietary_v1")
    mc, facts, conflicts = _get_db_counts()
    print(f"            Done in {time.time()-start:.1f}s — {mc} memcells, {facts} facts, {conflicts} conflicts")

    # Step 2: Ingest Szechuan hot pot (should trigger conflict)
    print("\n  [Step 2/3] Ingesting: user eats Szechuan hot pot (should trigger conflict)")
    conv2 = [
        {"role": "user", "content": "You won't believe what I had for dinner last night \u2014 a massive plate of extra spicy Szechuan hot pot."},
        {"role": "assistant", "content": "Wait, I thought you were exclusively eating continental cuisine? What happened?"},
        {"role": "user", "content": "Yeah I gave that up after about a month. I missed spicy Asian food too much. Back to my normal diet now."},
        {"role": "assistant", "content": "Ha, fair enough! At least you gave it a solid try."},
    ]
    start = time.time()
    _ingest_quiet(conv2, source_id="test_dietary_v2")
    mc, facts, conflicts = _get_db_counts()
    print(f"            Done in {time.time()-start:.1f}s — {mc} memcells, {facts} facts, {conflicts} conflicts")

    # Step 3: Query
    print("\n  [Step 3/3] Querying: \"What is the user's current diet?\"")
    result = agentic_retrieve("What is the user's current diet?", verbose=False)

    print()
    print("  ┌─ Result ─────────────────────────────────────────────")
    print(f"  │ Expected : No longer continental-only, back to normal diet including spicy Asian food")
    print(f"  │ Sufficient: {result['is_sufficient']}  |  Rounds: {result['rounds']}")
    foresight = result["result"].get("foresight", [])
    print(f"  │ Foresight : {len(foresight)} active")
    print(f"  │ Conflicts : {conflicts}")
    print(f"  │ PASS      : {'Yes' if conflicts > 0 else 'No — expected conflicts!'}")
    print("  └─────────────────────────────────────────────────────")


# ── Test: Temporary Health ──

def test_health():
    print()
    print("=" * 60)
    print("  TEST: Temporary Health (Foresight Expiry)")
    print("=" * 60)

    # Step 1: Ingest broken leg
    print("\n  [Step 1/3] Ingesting: user broke leg playing football (dated Feb 20)")
    conv = [
        {"role": "user", "content": "Feeling terrible today. I literally broke my leg playing football yesterday."},
        {"role": "assistant", "content": "Oh no! Have you seen a doctor yet?"},
        {"role": "user", "content": "Yeah, went to the ER. They put me in a cast and told me to rest for a few weeks."},
        {"role": "assistant", "content": "Make sure to keep weight off of it. Are you taking any time off work?"},
        {"role": "user", "content": "Taking the rest of the week off. My manager was understanding about it."},
        {"role": "assistant", "content": "Good. Rest up and keep that leg elevated!"},
    ]
    start = time.time()
    _ingest_quiet(conv, source_id="test_health_v1", current_date="2026-02-20")
    mc, facts, conflicts = _get_db_counts()
    print(f"            Done in {time.time()-start:.1f}s — {mc} memcells, {facts} facts")

    # Step 2: Query during recovery
    print("\n  [Step 2/3] Querying at Feb 25 (within recovery — should be active)")
    t1 = datetime.strptime("2026-02-25", "%Y-%m-%d")
    result1 = agentic_retrieve("Does the user have any injuries?", query_time=t1, verbose=False)
    foresight1 = result1["result"].get("foresight", [])

    print()
    print("  ┌─ Result ─────────────────────────────────────────────")
    print(f"  │ Expected  : Yes, broken leg in a cast")
    print(f"  │ Sufficient: {result1['is_sufficient']}  |  Rounds: {result1['rounds']}")
    print(f"  │ Foresight : {len(foresight1)} active")
    if foresight1:
        for fs in foresight1[:3]:
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            print(f"  │   → {fs['description'][:55]} (until {until})")
    print("  └─────────────────────────────────────────────────────")

    # Step 3: Query after expiry
    print("\n  [Step 3/3] Querying at Mar 20 (past recovery — should be expired)")
    t2 = datetime.strptime("2026-03-20", "%Y-%m-%d")
    result2 = agentic_retrieve("Does the user have any injuries?", query_time=t2, verbose=False)
    foresight2 = result2["result"].get("foresight", [])

    print()
    print("  ┌─ Result ─────────────────────────────────────────────")
    print(f"  │ Expected  : No, foresight should be expired")
    print(f"  │ Sufficient: {result2['is_sufficient']}  |  Rounds: {result2['rounds']}")
    print(f"  │ Foresight : {len(foresight2)} active")
    if foresight2:
        for fs in foresight2[:3]:
            until = fs["valid_until"].strftime("%Y-%m-%d") if fs.get("valid_until") else "indefinite"
            print(f"  │   → {fs['description'][:55]} (until {until})")
    else:
        print(f"  │   (no active foresight — expired as expected)")
    print("  └─────────────────────────────────────────────────────")


# ── Test: Job Change ──

def test_job():
    print()
    print("=" * 60)
    print("  TEST: Job Change (Profile Evolution)")
    print("=" * 60)

    # Step 1: Ingest Apple job
    print("\n  [Step 1/3] Ingesting: user works at Apple as hardware engineer")
    conv1 = [
        {"role": "user", "content": "Work has been amazing lately. I just finished designing a new chip layout for the M5 processor at Apple."},
        {"role": "assistant", "content": "That's impressive! What's your role on the team?"},
        {"role": "user", "content": "I'm a senior hardware engineer on the silicon design team. Been at Apple for about three years now."},
        {"role": "assistant", "content": "Three years is solid. How's the work environment?"},
        {"role": "user", "content": "Very secretive but the engineering culture is top notch. We use a lot of custom EDA tools and do everything in-house."},
        {"role": "assistant", "content": "That sounds like a unique experience. Apple's silicon team is legendary."},
        {"role": "user", "content": "Yeah, I love it. The hardware-software co-design approach is what keeps me here. Plus Cupertino is a nice place to live."},
        {"role": "assistant", "content": "Sounds like a great fit for you!"},
    ]
    start = time.time()
    _ingest_quiet(conv1, source_id="test_job_v1")
    mc, facts, conflicts = _get_db_counts()
    print(f"            Done in {time.time()-start:.1f}s — {mc} memcells, {facts} facts, {conflicts} conflicts")

    # Step 2: Ingest Nvidia job (should trigger conflict)
    print("\n  [Step 2/3] Ingesting: user joins Nvidia (should trigger conflict)")
    conv2 = [
        {"role": "user", "content": "I have some big news \u2014 I accepted an offer from Nvidia! Starting next month as a principal engineer."},
        {"role": "assistant", "content": "Wow, congratulations! What made you decide to leave Apple?"},
        {"role": "user", "content": "The GPU architecture team is doing incredible work on next-gen AI accelerators. I'll be leading the chip design division."},
        {"role": "assistant", "content": "That's a huge opportunity. Nvidia is at the forefront of AI hardware."},
        {"role": "user", "content": "Yeah, I'm really excited. It's a step up to principal engineer and I'll be relocating to Santa Clara."},
        {"role": "assistant", "content": "That's not too far from Cupertino. I'm sure you'll do great there!"},
    ]
    start = time.time()
    _ingest_quiet(conv2, source_id="test_job_v2")
    mc, facts, conflicts = _get_db_counts()
    print(f"            Done in {time.time()-start:.1f}s — {mc} memcells, {facts} facts, {conflicts} conflicts")

    # Step 3: Query
    print("\n  [Step 3/3] Querying: \"Where does the user currently work?\"")
    result = agentic_retrieve("Where does the user currently work?", verbose=False)

    profile = db.get_user_profile()
    has_nvidia = profile and any('nvidia' in f.lower() for f in profile.explicit_facts)

    print()
    print("  ┌─ Result ─────────────────────────────────────────────")
    print(f"  │ Expected  : Nvidia (principal engineer)")
    print(f"  │ Sufficient: {result['is_sufficient']}  |  Rounds: {result['rounds']}")
    print(f"  │ Conflicts : {conflicts}")
    if profile:
        print(f"  │ Profile   :")
        for fact in profile.explicit_facts[:5]:
            print(f"  │   → {fact[:60]}")
    print(f"  │ PASS      : {'Yes' if has_nvidia else 'No — expected Nvidia in profile!'}")
    print("  └─────────────────────────────────────────────────────")


# ── Main ──

def main():
    global VERBOSE
    VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

    db.init_schema()
    vector_store.init_collections()

    if len(sys.argv) < 2:
        print(__doc__)
        return

    test_name = sys.argv[1]
    no_reset = "--no-reset" in sys.argv

    if test_name in ("dietary", "all"):
        if not no_reset:
            print("Resetting databases...")
            reset_databases()
            print("Done.\n")
        test_dietary()

    if test_name in ("health", "all"):
        if not no_reset and test_name != "all":
            print("Resetting databases...")
            reset_databases()
            print("Done.\n")
        test_health()

    if test_name in ("job", "all"):
        if not no_reset and test_name != "all":
            print("Resetting databases...")
            reset_databases()
            print("Done.\n")
        test_job()

    if test_name not in ("dietary", "health", "job", "all"):
        print(f"Unknown test: {test_name}")
        print(__doc__)


if __name__ == "__main__":
    main()
