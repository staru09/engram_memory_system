import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import argparse
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

CATEGORY_NAMES = {
    1: "single-hop",
    2: "temporal",
    3: "multi-hop",
    4: "open-domain",
    5: "adversarial",
}


def parse_locomo_date(date_str: str) -> str:
    if not date_str:
        return "2024-01-01"
    try:
        if " on " in date_str:
            date_part = date_str.split(" on ", 1)[1].strip()
        else:
            date_part = date_str.strip()
        date_part = date_part.replace(",", "")
        dt = datetime.strptime(date_part, "%d %B %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "2024-01-01"


def load_conversation(sample_idx: int) -> dict:
    with open("locomo/data/locomo10.json") as f:
        data = json.load(f)
    return data[sample_idx]


def ingest_conversation(sample: dict):
    from main import ingest_conversation as run_ingestion

    conv = sample["conversation"]
    speaker_a = conv.get("speaker_a", "User")
    speaker_b = conv.get("speaker_b", "Assistant")

    sessions = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("date_time")],
        key=lambda s: int(s.split("_")[1])
    )

    print(f"\n=== INGESTING ===")
    print(f"  Speaker A: {speaker_a}")
    print(f"  Speaker B: {speaker_b}")
    print(f"  Sessions: {len(sessions)}")

    total_turns = 0
    for i, session_key in enumerate(sessions):
        date_key = f"{session_key}_date_time"
        raw_date = conv.get(date_key, "2024-01-01")
        session_date = parse_locomo_date(raw_date)

        msgs = []
        for turn in conv[session_key]:
            speaker = turn["speaker"]
            role = "user" if speaker == speaker_a else "assistant"
            msgs.append({
                "role": role,
                "content": f"{speaker}: {turn['text']}",
                "created_at": session_date,
            })

        total_turns += len(msgs)
        source_id = f"locomo_{session_key}"

        print(f"\n  Session {i+1}/{len(sessions)}: {session_key} ({len(msgs)} turns, date: {session_date})")

        try:
            run_ingestion(msgs, source_id=source_id, current_date=session_date,
                          extract_all_speakers=True)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            continue

    print(f"\n=== INGESTION COMPLETE ===")
    print(f"  Total turns ingested: {total_turns}")

    import db
    stats = db.get_system_stats()
    print(f"  Conflicts: {stats['total_conflicts']}")
    print(f"  Active foresight: {stats['active_foresight']}")
    print(f"  Has profile: {stats['has_profile']}")


def _get_last_session_date(sample: dict) -> datetime:
    conv = sample["conversation"]
    session_keys = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("date_time")],
        key=lambda s: int(s.split("_")[1])
    )
    if not session_keys:
        return datetime.now(IST).replace(tzinfo=None)
    last_date_key = f"{session_keys[-1]}_date_time"
    raw_date = conv.get(last_date_key, "")
    date_str = parse_locomo_date(raw_date)
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return datetime.now(IST).replace(tzinfo=None)


def _process_single_qa(qa: dict, index: int, total: int,
                       client, model, query_time=None) -> dict:
    from agentic_layer.fetch_mem_service import retrieve, compose_context

    question = qa["question"]
    ground_truth = qa.get("answer", qa.get("adversarial_answer", ""))
    category = qa.get("category", 0)
    cat_name = CATEGORY_NAMES.get(category, "unknown")

    # Retrieve
    t0 = time.time()
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)
    try:
        result = retrieve(question, query_time)
        context = compose_context(result)
    except Exception as e:
        print(f"  [{index+1}/{total}] ({cat_name}) RETRIEVAL ERROR: {e}")
        return {
            "question": question, "ground_truth": str(ground_truth),
            "answer": "ERROR", "category": category, "cat_name": cat_name,
            "retrieval_time": 0, "llm_time": 0, "score": 0,
        }
    retrieval_time = time.time() - t0

    # Generate answer
    t0 = time.time()
    prompt = f"""You are answering questions about a long-term conversation between two people. Use ONLY the provided context.

ANSWERING RULES:
- Keep your answer short and precise (1-2 sentences max).
- Be SPECIFIC: include exact names, dates, places, numbers, titles when available.
- For "would someone do X" questions: reason from their stated interests, career goals, and preferences. If they previously expressed interest in something but circumstances changed, favour the most recent signal.
- Trust the MOST RECENT information when facts conflict.
- Do NOT make up facts. Only use what is explicitly in the context.
- If the context genuinely doesn't contain the answer, say "I don't know."
- IMPORTANT: Prefer giving a partial answer over "I don't know." If you can find ANY relevant information, share it.

Context:
{context}

Question: {question}

Answer:"""

    try:
        response = client.models.generate_content(model=model, contents=prompt)
        answer = response.text.strip()
    except Exception as e:
        answer = f"ERROR: {e}"
    llm_time = time.time() - t0

    # Score using LLM-as-judge
    try:
        judge_prompt = f"""Compare the generated answer against the ground truth.
Score from 1-5:
5 = Perfect match or semantically equivalent
4 = Mostly correct, minor details different
3 = Partially correct, has the right idea but missing key details
2 = Mostly wrong but has some relevant information
1 = Completely wrong or irrelevant

Question: {question}
Ground Truth: {ground_truth}
Generated Answer: {answer}

Return ONLY a JSON: {{"score": N, "reasoning": "brief explanation"}}"""

        judge_response = client.models.generate_content(model=model, contents=judge_prompt)
        judge_text = judge_response.text.strip()
        if judge_text.startswith("```"):
            judge_text = judge_text.split("\n", 1)[1]
            if judge_text.endswith("```"):
                judge_text = judge_text[:-3]
        judge_result = json.loads(judge_text)
        score = judge_result.get("score", 0)
        reasoning = judge_result.get("reasoning", "")
    except Exception:
        score = 0
        reasoning = "judge failed"

    print(f"  [{index+1}/{total}] ({cat_name}) Score: {score}/5 | R: {retrieval_time:.2f}s | L: {llm_time:.2f}s | {question[:50]}...")
    if score <= 2:
        print(f"    GT:  {str(ground_truth)[:80]}")
        print(f"    Got: {answer[:80]}")

    return {
        "question": question,
        "ground_truth": str(ground_truth),
        "answer": answer,
        "category": category,
        "cat_name": cat_name,
        "retrieval_time": retrieval_time,
        "llm_time": llm_time,
        "score": score,
        "reasoning": reasoning,
    }


def run_qa(sample: dict, limit: int = None, parallel_workers: int = 5) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from google import genai
    from config import GEMINI_API_KEY, GEMINI_MODEL

    client = genai.Client(api_key=GEMINI_API_KEY)

    qa_pairs = [q for q in sample["qa"] if q.get("category") != 5]
    if limit:
        qa_pairs = qa_pairs[:limit]

    query_time = _get_last_session_date(sample)
    print(f"  Temporal reference date: {query_time.strftime('%Y-%m-%d')} (last session)")

    total = len(qa_pairs)
    skipped = len(sample["qa"]) - len(qa_pairs)
    print(f"\n=== RUNNING QA ({total} questions, {skipped} adversarial skipped, {parallel_workers} workers) ===\n")

    results = [None] * total

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        futures = {}
        for i, qa in enumerate(qa_pairs):
            future = executor.submit(
                _process_single_qa, qa, i, total, client, GEMINI_MODEL, query_time
            )
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                print(f"  [{idx+1}/{total}] FAILED: {e}")
                results[idx] = {
                    "question": qa_pairs[idx]["question"],
                    "ground_truth": str(qa_pairs[idx].get("answer", "")),
                    "answer": "ERROR", "category": qa_pairs[idx].get("category", 0),
                    "cat_name": CATEGORY_NAMES.get(qa_pairs[idx].get("category", 0), "unknown"),
                    "retrieval_time": 0, "llm_time": 0, "score": 0,
                }

    results = [r for r in results if r is not None]
    return results


def print_report_tagged(results: list[dict], tag: str):
    print(f"\n{'=' * 70}")
    print(f"  LOCOMO BENCHMARK RESULTS  [{tag}]")
    print(f"{'=' * 70}\n")

    non_adv_results = [r for r in results if r["category"] != 5]
    adv_results = [r for r in results if r["category"] == 5]

    scores = [r["score"] for r in non_adv_results if r["score"] > 0]
    avg_score = sum(scores) / len(scores) if scores else 0
    retrieval_times = [r["retrieval_time"] for r in non_adv_results]
    avg_retrieval = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0

    print(f"  Overall (excluding adversarial):")
    print(f"    Questions: {len(non_adv_results)} (+ {len(adv_results)} adversarial excluded)")
    print(f"    Avg Score: {avg_score:.2f}/5")
    print(f"    Score >= 4: {sum(1 for s in scores if s >= 4)}/{len(scores)} ({sum(1 for s in scores if s >= 4)/len(scores)*100:.1f}%)")
    print(f"    Score >= 3: {sum(1 for s in scores if s >= 3)}/{len(scores)} ({sum(1 for s in scores if s >= 3)/len(scores)*100:.1f}%)")
    print(f"    Avg Retrieval: {avg_retrieval:.2f}s")

    print(f"\n  Per Category:")
    print(f"    {'Category':<15} {'Count':>6} {'Avg Score':>10} {'>=4':>6} {'>=3':>6}")
    print(f"    {'-'*15} {'-'*6} {'-'*10} {'-'*6} {'-'*6}")

    for cat_num in sorted(CATEGORY_NAMES.keys()):
        if cat_num == 5:
            continue
        cat_results = [r for r in results if r["category"] == cat_num and r["score"] > 0]
        if not cat_results:
            continue
        cat_scores = [r["score"] for r in cat_results]
        avg = sum(cat_scores) / len(cat_scores)
        gte4 = sum(1 for s in cat_scores if s >= 4)
        gte3 = sum(1 for s in cat_scores if s >= 3)
        name = CATEGORY_NAMES[cat_num]
        print(f"    {name:<15} {len(cat_results):>6} {avg:>10.2f} {gte4:>5} {gte3:>5}")

    worst = sorted([r for r in non_adv_results if r["score"] > 0], key=lambda x: x["score"])[:5]
    if worst:
        print(f"\n  Worst Answers:")
        for r in worst:
            print(f"    [{r['cat_name']}] Score {r['score']}/5: {r['question'][:60]}")
            print(f"      GT:  {r['ground_truth'][:60]}")
            print(f"      Got: {r['answer'][:60]}")
            print()

    output_path = f"benchmark_results/gpt_style_{tag}.json"
    os.makedirs("benchmark_results", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  Full results saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run LoCoMo benchmark")
    parser.add_argument("--sample", type=int, default=0, help="Sample index (0-9)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of QA pairs")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion")
    parser.add_argument("--reset", action="store_true", help="Reset DB before ingestion")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers for QA")
    args = parser.parse_args()

    sample = load_conversation(args.sample)
    print(f"Loaded sample {args.sample}: {len(sample['qa'])} QA pairs")

    if args.reset:
        from main import reset_databases
        reset_databases()

    if not args.skip_ingest:
        ingest_conversation(sample)

    results = run_qa(sample, limit=args.limit, parallel_workers=args.workers)
    print_report_tagged(results, "gpt_style")


if __name__ == "__main__":
    main()
