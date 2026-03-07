import sys
import os
import time
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
import db
import vector_store
from agentic_layer.memory_manager import agentic_retrieve

_llm_client = genai.Client(api_key=GEMINI_API_KEY)


def _generate_answer(query: str, context: str) -> str:
    """Use retrieved context to generate a concise natural language answer."""
    prompt = (
        f"Based ONLY on the following retrieved memory context, answer the question concisely in 2-3 sentences.\n"
        f"If the context doesn't contain relevant information, say 'No relevant information found.'\n"
        f"IMPORTANT: The 'Top Matching Facts' section contains the most reliable, temporally-filtered information. "
        f"If episode narratives mention something that contradicts or goes beyond the facts, trust the facts. "
        f"Only report what is CURRENTLY true — do not mention past states, previous values, or historical context "
        f"unless the question explicitly asks about history.\n\n"
        f"Question: {query}\n\n"
        f"Context:\n{context}\n\n"
        f"Answer:"
    )
    response = _llm_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text.strip()

# ── Stage-specific expected results ──
# Same 10 queries, different expected answers per stage.

STAGE_DEFAULTS = {
    1: "2026-02-01",
    2: "2026-03-01",
    3: "2026-05-01",
    4: "2026-06-15",
}

QUERIES = {
    1: [
        {
            "category": "Basic Extraction",
            "query": "What does Aru study and where?",
            "expected": "Junior in Mechanical Engineering at Stanford. Taking ME 101 (thermodynamics) and ME 131 (fluid mechanics).",
        },
        {
            "category": "Basic Extraction",
            "query": "What kind of diet does Aru follow?",
            "expected": "Strict vegetarian. Cooks Indian food (dal, sabzi, paneer). Never eats meat, chicken, or fish.",
        },
        {
            "category": "Basic Extraction",
            "query": "Where does Aru currently live?",
            "expected": "Wilbur Hall, on-campus at Stanford.",
        },
        {
            "category": "Scene Formation",
            "query": "What are Aru's hobbies and interests?",
            "expected": "Basketball (Arrillaga gym weekends), acoustic guitar, robotics club (building competition bot). Runs 3 miles daily.",
        },
        {
            "category": "Scene Formation",
            "query": "Does Aru have any upcoming travel plans?",
            "expected": "Spring break trip to Yosemite planned with friend Jake in late March.",
        },
        {
            "category": "Simple Retrieval",
            "query": "What do you know about Aru overall?",
            "expected": "Stanford ME junior, lives in Wilbur Hall, strict vegetarian, plays basketball and guitar, in robotics club, planning Yosemite trip.",
        },
    ],
    2: [
        {
            "category": "Conflict Detection",
            "query": "What kind of diet does Aru currently follow?",
            "expected": "No longer vegetarian. Now eats chicken and fish (doctor's orders for ACL recovery protein). Conflict should supersede vegetarian.",
        },
        {
            "category": "Conflict Detection",
            "query": "Where is Aru currently working or interning?",
            "expected": "No internship yet. Still a student but pivoted from ME to CS/ML (taking CS 229).",
        },
        {
            "category": "Profile Evolution",
            "query": "What is Aru's academic and internship history?",
            "expected": "Was ME student (ME 101, ME 131). Dropped ME 131, now taking CS 229 (Machine Learning). Pivoting to CS/ML.",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Does Aru have any active health issues or injuries?",
            "expected": "Torn ACL (Feb 20). Doing physical therapy 3x/week. No sports for 2 months (until April 20).",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Is Aru currently on any medication?",
            "expected": "Anti-inflammatory meds (started Feb 20, prescribed 2 weeks until March 6). Should still be active at query time March 1.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "What are Aru's hobbies and interests?",
            "expected": "Robotics club (still active). Guitar (still active, not contradicted). No basketball (ACL injury). Can't run or do sports.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "Does Aru have any upcoming travel plans?",
            "expected": "Yosemite cancelled due to injury. Planning summer trip to Tokyo instead.",
        },
        {
            "category": "Profile Evolution",
            "query": "Where does Aru currently live?",
            "expected": "University Ave apartment (off-campus) with Jake. No longer in Wilbur Hall. Conflict should supersede.",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Can Aru play sports this week?",
            "expected": "No. Torn ACL, doctor restricted all sports for 2 months (until April 20). Cannot run or play basketball.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "What do you know about Aru overall?",
            "expected": "Stanford student, torn ACL recovery, pivoted to CS/ML, moved to University Ave, no longer vegetarian, Tokyo trip planned.",
        },
    ],
    3: [
        {
            "category": "Conflict Detection",
            "query": "What kind of diet does Aru currently follow?",
            "expected": "Still eating chicken and fish (not vegetarian). No new diet change in stage 3.",
        },
        {
            "category": "Conflict Detection",
            "query": "Where is Aru currently working or interning?",
            "expected": "Summer research internship at Stanford AI Lab (SAIL) under Prof. Fei-Fei Li. Computer vision for robotics. Starting June.",
        },
        {
            "category": "Profile Evolution",
            "query": "What is Aru's academic and internship history?",
            "expected": "ME student → dropped ME 131 → CS 229 pivot → SAIL internship (computer vision for robotics).",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Does Aru have any active health issues or injuries?",
            "expected": "No. ACL fully healed (cleared April 20). Stopped physical therapy. Running again. No injuries.",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Is Aru currently on any medication?",
            "expected": "No. Anti-inflammatory meds expired (ended March 6). No current medication.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "What are Aru's hobbies and interests?",
            "expected": "Bouldering (new), robotics club (leads software/perception team). Quit guitar. Back to running. Basketball possible again.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "Does Aru have any upcoming travel plans?",
            "expected": "Tokyo trip confirmed for August. Flights and Airbnb booked.",
        },
        {
            "category": "Profile Evolution",
            "query": "Where does Aru currently live?",
            "expected": "Still on University Ave apartment with Jake.",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Can Aru play sports this week?",
            "expected": "Yes. ACL fully healed, doctor cleared, back to running and basketball.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "What do you know about Aru overall?",
            "expected": "Stanford CS/ML student, ACL healed, SAIL internship, bouldering, quit guitar, lives on University Ave, Tokyo trip in August.",
        },
    ],
    4: [
        {
            "category": "Conflict Detection",
            "query": "What kind of diet does Aru currently follow?",
            "expected": "Fully non-vegetarian. Meal-preps chicken and rice. No change from stage 2+ but reinforced.",
        },
        {
            "category": "Conflict Detection",
            "query": "Where is Aru currently working or interning?",
            "expected": "Tesla Autopilot team. Left SAIL. Conflict should supersede SAIL internship.",
        },
        {
            "category": "Profile Evolution",
            "query": "What is Aru's academic and internship history?",
            "expected": "ME courses → CS 229 pivot → SAIL research → Tesla Autopilot. Full progression visible.",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Does Aru have any active health issues or injuries?",
            "expected": "Food poisoning (June 10). On antibiotics for 5 days (until June 15). Depends on query time whether active.",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Is Aru currently on any medication?",
            "expected": "Antibiotics for food poisoning (5 days, June 10-15). Should be active/expiring at query time June 15.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "What are Aru's hobbies and interests?",
            "expected": "Surfing at Pacifica (new), bouldering (occasional), robotics club. No guitar. No basketball mentioned recently.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "Does Aru have any upcoming travel plans?",
            "expected": "Tokyo trip in August still planned.",
        },
        {
            "category": "Profile Evolution",
            "query": "Where does Aru currently live?",
            "expected": "Palo Alto (relocated for Tesla). No longer on University Ave. Conflict should supersede.",
        },
        {
            "category": "Foresight / Temporal",
            "query": "Can Aru play sports this week?",
            "expected": "Yes. No sports-related injuries. Food poisoning doesn't restrict sports.",
        },
        {
            "category": "Retrieval Relevance",
            "query": "What do you know about Aru overall?",
            "expected": "Stanford student interning at Tesla Autopilot, lives in Palo Alto, non-vegetarian, surfs at Pacifica, Tokyo trip in August.",
        },
    ],
}


def run_evaluation(stage: int, query_time: datetime, tag: str = None):
    db.init_schema()
    vector_store.init_collections()

    queries = QUERIES.get(stage)
    if not queries:
        print(f"Unknown stage: {stage}. Available: {list(QUERIES.keys())}")
        return

    # Gather DB stats
    stats = db.get_system_stats()

    # Build output filename
    os.makedirs("results_testing", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_part = f"_{tag}" if tag else f"_stage{stage}"
    filename = f"results_testing/eval{tag_part}_{timestamp}.md"

    results = []
    total_latency = 0.0

    print(f"Running {len(queries)} evaluation queries for Stage {stage} (query_time={query_time.strftime('%Y-%m-%d')})...\n")

    for i, q in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] {q['query']}")

        start = time.time()
        result = agentic_retrieve(q["query"], query_time=query_time, verbose=False)
        latency = time.time() - start
        total_latency += latency

        # Generate a concise answer from retrieved context
        generated_answer = _generate_answer(q["query"], result["context"]) if result["context"] else "No context retrieved."
        gen_latency = time.time() - start
        total_latency += (gen_latency - latency)  # add answer generation time
        latency = gen_latency

        print(f"           Sufficient: {result['is_sufficient']} | Rounds: {result['rounds']} | {latency:.1f}s")
        print(f"           Answer: {generated_answer[:80]}...")

        results.append({
            **q,
            "context": result["context"],
            "generated_answer": generated_answer,
            "is_sufficient": result["is_sufficient"],
            "rounds": result["rounds"],
            "latency": latency,
            "episodes_found": len(result["result"].get("episodes", [])),
            "facts_found": len(result["result"].get("facts", [])),
            "foresight_active": len(result["result"].get("foresight", [])),
        })

    # Write markdown report
    avg_latency = total_latency / len(queries)

    lines = []
    lines.append(f"# Evaluation Report — Stage {stage}{f' ({tag})' if tag else ''}")
    lines.append(f"")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Query Time:** {query_time.strftime('%Y-%m-%d')}")
    lines.append(f"**Stage:** {stage}")
    lines.append(f"")
    lines.append(f"## Database State")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| MemCells | {stats['total_memcells']} |")
    lines.append(f"| MemScenes | {stats['total_scenes']} |")
    lines.append(f"| Active Facts | {stats['active_facts']} |")
    lines.append(f"| Total Facts | {stats['total_facts']} |")
    lines.append(f"| Conflicts Detected | {stats['total_conflicts']} |")
    lines.append(f"| Deduplication Rate | {((stats['total_facts'] - stats['active_facts']) / stats['total_facts'] * 100) if stats['total_facts'] > 0 else 0:.1f}% |")
    lines.append(f"")
    lines.append(f"## Summary")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Queries Run | {len(queries)} |")
    lines.append(f"| Sufficient (Round 1) | {sum(1 for r in results if r['is_sufficient'] and r['rounds'] == 1)} / {len(queries)} |")
    lines.append(f"| Sufficient (After Rewrite) | {sum(1 for r in results if r['is_sufficient'])} / {len(queries)} |")
    lines.append(f"| Avg Latency | {avg_latency:.1f}s |")
    lines.append(f"| Total Time | {total_latency:.1f}s |")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    for i, r in enumerate(results, 1):
        status = "SUFFICIENT" if r["is_sufficient"] else "INSUFFICIENT"
        lines.append(f"## Q{i}: {r['query']}")
        lines.append(f"**Category:** {r['category']}  ")
        lines.append(f"**Expected:** {r['expected']}  ")
        lines.append(f"**Status:** {status} | Rounds: {r['rounds']} | Latency: {r['latency']:.1f}s  ")
        lines.append(f"**Retrieved:** {r['episodes_found']} episodes, {r['facts_found']} facts, {r['foresight_active']} active foresight")
        lines.append(f"")
        lines.append(f"**Generated Answer:** {r['generated_answer']}")
        lines.append(f"")
        lines.append(f"<details>")
        lines.append(f"<summary>Full Retrieved Context</summary>")
        lines.append(f"")
        lines.append(f"```")
        lines.append(r["context"] if r["context"] else "(empty context)")
        lines.append(f"```")
        lines.append(f"</details>")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nResults saved to {filename}")
    print(f"  Sufficient: {sum(1 for r in results if r['is_sufficient'])}/{len(queries)}")
    print(f"  Avg latency: {avg_latency:.1f}s")

    return filename


def main():
    stage = None
    query_time = None
    tag = None

    args = sys.argv[1:]

    if "--all" in args:
        print("Running all 4 stage evaluations...\n")
        for s in sorted(STAGE_DEFAULTS.keys()):
            qt = datetime.strptime(STAGE_DEFAULTS[s], "%Y-%m-%d")
            print(f"\n{'#'*60}")
            print(f"# STAGE {s} (query_time={STAGE_DEFAULTS[s]})")
            print(f"{'#'*60}\n")
            run_evaluation(s, qt)
        return

    if "--stage" in args:
        idx = args.index("--stage") + 1
        if idx < len(args):
            stage = int(args[idx])

    if "--time" in args:
        idx = args.index("--time") + 1
        if idx < len(args):
            query_time = datetime.strptime(args[idx], "%Y-%m-%d")

    if "--tag" in args:
        idx = args.index("--tag") + 1
        if idx < len(args):
            tag = args[idx]

    if stage is None:
        print("Error: --stage or --all is required")
        print(__doc__)
        return

    if query_time is None:
        default_date = STAGE_DEFAULTS.get(stage)
        if default_date:
            query_time = datetime.strptime(default_date, "%Y-%m-%d")
        else:
            query_time = datetime.now()

    run_evaluation(stage, query_time, tag)


if __name__ == "__main__":
    main()
