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
    """Parse LoCoMo date format to YYYY-MM-DD.
    Input:  '8:56 pm on 20 July, 2023'
    Output: '2023-07-20'
    """
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
    """Ingest all sessions sequentially using v1 pipeline."""
    from main import ingest_conversation as run_ingestion

    conv = sample["conversation"]
    speaker_a = conv.get("speaker_a", "User")
    speaker_b = conv.get("speaker_b", "Assistant")

    sessions = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("date_time")],
        key=lambda s: int(s.split("_")[1])
    )

    print(f"\n=== INGESTING (v1, per-session sequential) ===")
    print(f"  Speaker A (user): {speaker_a}")
    print(f"  Speaker B (assistant): {speaker_b}")
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
    print(f"  Facts: {stats['active_facts']} active / {stats['total_facts']} total")
    print(f"  MemCells: {stats['total_memcells']}")
    print(f"  Scenes: {stats['total_scenes']}")
    print(f"  Conflicts: {stats['total_conflicts']}")


def _process_single_qa(qa: dict, index: int, total: int, use_fast: bool,
                       client, model, query_time=None) -> dict:
    """Process a single QA pair: retrieve -> answer -> judge. Thread-safe."""
    from agentic_layer.fetch_mem_service import retrieve_fast, compose_context_fast, compose_context

    question = qa["question"]
    ground_truth = qa.get("answer", qa.get("adversarial_answer", ""))
    category = qa.get("category", 0)
    cat_name = CATEGORY_NAMES.get(category, "unknown")

    # Retrieve
    t0 = time.time()
    if query_time is None:
        query_time = datetime.now(IST).replace(tzinfo=None)
    try:
        if use_fast:
            result = retrieve_fast(question, query_time)
            context = compose_context_fast(result)
        else:
            result = retrieve_fast(question, query_time)
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
        "facts_found": len(result.get("facts", [])),
    }


def _get_last_session_date(sample: dict) -> datetime:
    """Return the date of the last session as a naive datetime (used as query_time reference).
    This ensures temporal expressions like 'in July' resolve relative to the conversation's
    timeframe rather than today's date."""
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


def run_qa(sample: dict, use_fast: bool = True, limit: int = None,
           parallel_workers: int = 5) -> list[dict]:
    """Run QA pairs through retrieval pipeline and score answers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from google import genai
    from config import GEMINI_API_KEY, GEMINI_MODEL

    client = genai.Client(api_key=GEMINI_API_KEY)

    qa_pairs = [q for q in sample["qa"] if q.get("category") != 5]  # exclude adversarial
    if limit:
        qa_pairs = qa_pairs[:limit]

    # Use last session date as reference for temporal resolution.
    # "in July" should resolve to July 2023 (conversation timeframe), not July 2025.
    query_time = _get_last_session_date(sample)
    print(f"  Temporal reference date: {query_time.strftime('%Y-%m-%d')} (last session)")

    total = len(qa_pairs)
    skipped = len(sample["qa"]) - len(qa_pairs)
    print(f"\n=== RUNNING QA ({total} questions, {skipped} adversarial skipped, {'fast' if use_fast else 'normal'} mode, {parallel_workers} workers) ===\n")

    results = [None] * total

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        futures = {}
        for i, qa in enumerate(qa_pairs):
            future = executor.submit(
                _process_single_qa, qa, i, total, use_fast, client, GEMINI_MODEL, query_time
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


def print_report(results: list[dict]):
    """Print evaluation report."""
    print(f"\n{'=' * 70}")
    print(f"  LOCOMO BENCHMARK RESULTS")
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
            continue  # skip adversarial
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

    output_path = "benchmark_results/locomo_results_local_fast.json"
    os.makedirs("benchmark_results", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  Full results saved to {output_path}")


def print_report_tagged(results: list[dict], tag: str):
    """Print report and save to a tagged output file (fast/normal + branch label)."""
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

    output_path = f"benchmark_results/memU_category_update_prompt_{tag}.json"
    os.makedirs("benchmark_results", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  Full results saved to {output_path}")


def _get_last_session_date(sample: dict) -> datetime:
    """Return the last session date as a naive datetime.
    Used as the temporal reference so month-name queries like 'in July'
    resolve to the conversation's timeframe, not today's date."""
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


def _generate_report_data(results: list[dict], tag: str) -> dict:
    """Generate structured report data from results."""
    non_adv = [r for r in results if r["category"] != 5]
    scores = [r["score"] for r in non_adv if r["score"] > 0]
    retrieval_times = [r["retrieval_time"] for r in non_adv]

    avg_score = sum(scores) / len(scores) if scores else 0
    avg_retrieval = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0
    gte4 = sum(1 for s in scores if s >= 4)
    gte3 = sum(1 for s in scores if s >= 3)

    per_category = {}
    for cat_num in sorted(CATEGORY_NAMES.keys()):
        if cat_num == 5:
            continue
        cat_results = [r for r in results if r["category"] == cat_num and r["score"] > 0]
        if not cat_results:
            continue
        cat_scores = [r["score"] for r in cat_results]
        per_category[CATEGORY_NAMES[cat_num]] = {
            "count": len(cat_results),
            "avg_score": round(sum(cat_scores) / len(cat_scores), 2),
            "gte4": sum(1 for s in cat_scores if s >= 4),
            "gte3": sum(1 for s in cat_scores if s >= 3),
        }

    worst = sorted([r for r in non_adv if r["score"] > 0], key=lambda x: x["score"])[:5]

    return {
        "tag": tag,
        "total_questions": len(non_adv),
        "avg_score": round(avg_score, 2),
        "pct_score": round(avg_score / 5 * 100, 1),
        "gte4": gte4,
        "gte4_pct": round(gte4 / len(scores) * 100, 1) if scores else 0,
        "gte3": gte3,
        "gte3_pct": round(gte3 / len(scores) * 100, 1) if scores else 0,
        "avg_retrieval": round(avg_retrieval, 2),
        "per_category": per_category,
        "worst": worst,
    }


def _write_session_markdown(md_path: str, session_key: str, session_date: str,
                            fast_report: dict, normal_report: dict):
    """Append one session's results to the markdown file."""
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(f"\n## After {session_key} ({session_date})\n\n")

        for report in [fast_report, normal_report]:
            tag = report["tag"]
            f.write(f"### {tag.upper()} Mode\n\n")
            f.write(f"| Metric | Value |\n|--------|-------|\n")
            f.write(f"| Avg Score | {report['avg_score']}/5 ({report['pct_score']}%) |\n")
            f.write(f"| Score >= 4 | {report['gte4']}/{report['total_questions']} ({report['gte4_pct']}%) |\n")
            f.write(f"| Score >= 3 | {report['gte3']}/{report['total_questions']} ({report['gte3_pct']}%) |\n")
            f.write(f"| Avg Retrieval | {report['avg_retrieval']}s |\n\n")

            f.write(f"| Category | Count | Avg Score | >=4 | >=3 |\n")
            f.write(f"|----------|-------|-----------|-----|-----|\n")
            for cat_name, cat_data in report["per_category"].items():
                f.write(f"| {cat_name} | {cat_data['count']} | {cat_data['avg_score']} | {cat_data['gte4']} | {cat_data['gte3']} |\n")
            f.write("\n")

            if report["worst"]:
                f.write(f"**Worst answers:**\n\n")
                for r in report["worst"][:3]:
                    f.write(f"- [{r['cat_name']}] Score {r['score']}/5: {r['question'][:60]}\n")
                    f.write(f"  - GT: {r['ground_truth'][:60]}\n")
                    f.write(f"  - Got: {r['answer'][:60]}\n")
                f.write("\n")

        f.write("---\n")


def _reset_databases():
    """Reset all databases for a fresh run."""
    from main import reset_databases
    reset_databases()
    import db
    db.init_schema()
    import vector_store
    vector_store.init_collections()


def main():
    parser = argparse.ArgumentParser(description="Run LoCoMo benchmark")
    parser.add_argument("--sample", type=int, default=0, help="Sample index (0-9)")
    parser.add_argument("--fast", action="store_true", help="Run fast mode only")
    parser.add_argument("--normal", action="store_true", help="Run normal mode only")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of QA pairs")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion (use existing data)")
    parser.add_argument("--reset", action="store_true", help="Reset DB before ingestion")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers for QA (default: 5)")
    parser.add_argument("--per-session", action="store_true", help="Run QA after each session and write results to markdown")
    parser.add_argument("--skip-benchmark", action="store_true", help="Skip benchmark (ingest only)")
    args = parser.parse_args()

    sample = load_conversation(args.sample)
    print(f"Loaded sample {args.sample}: {len(sample['qa'])} QA pairs")

    if args.reset:
        print("\nResetting databases...")
        _reset_databases()

    # --- Per-session mode: ingest one session, run QA, write results, repeat ---
    if args.per_session:
        from main import ingest_conversation as run_ingestion

        conv = sample["conversation"]
        speaker_a = conv.get("speaker_a", "User")
        speaker_b = conv.get("speaker_b", "Assistant")
        sessions = sorted(
            [k for k in conv if k.startswith("session_") and not k.endswith("date_time")],
            key=lambda s: int(s.split("_")[1])
        )

        # Reset DB for fresh run
        print("\nResetting databases for per-session run...")
        _reset_databases()

        # Prepare markdown output
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_path = f"benchmark_results/per_session_sample{args.sample}_{timestamp}.md"
        os.makedirs("benchmark_results", exist_ok=True)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# Per-Session Benchmark Results\n\n")
            f.write(f"- **Sample**: {args.sample} ({speaker_a} & {speaker_b})\n")
            f.write(f"- **Sessions**: {len(sessions)}\n")
            f.write(f"- **QA Pairs**: {len([q for q in sample['qa'] if q.get('category') != 5])} (excl. adversarial)\n")
            f.write(f"- **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"---\n")

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

            print(f"\n{'=' * 70}")
            print(f"  SESSION {i+1}/{len(sessions)}: {session_key} ({len(msgs)} turns, date: {session_date})")
            print(f"{'=' * 70}")

            # Ingest this session
            try:
                run_ingestion(msgs, source_id=source_id, current_date=session_date,
                              extract_all_speakers=True)
            except Exception as e:
                print(f"  INGESTION ERROR: {e}")
                import traceback
                traceback.print_exc()
                continue

            # Run QA in both modes
            query_time = _get_last_session_date(sample)

            print(f"\n  --- Running FAST mode QA ---")
            fast_results = run_qa(sample, use_fast=True, limit=args.limit,
                                  parallel_workers=args.workers)
            fast_report = _generate_report_data(fast_results, "fast")
            print_report_tagged(fast_results, f"fast_after_{session_key}")

            print(f"\n  --- Running NORMAL mode QA ---")
            normal_results = run_qa(sample, use_fast=False, limit=args.limit,
                                    parallel_workers=args.workers)
            normal_report = _generate_report_data(normal_results, "normal")
            print_report_tagged(normal_results, f"normal_after_{session_key}")

            # Write to markdown
            _write_session_markdown(md_path, session_key, session_date, fast_report, normal_report)

            print(f"\n  Results appended to {md_path}")

        # Write final summary
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(f"\n## Final Stats\n\n")
            f.write(f"- Total turns ingested: {total_turns}\n")
            import db
            stats = db.get_system_stats()
            f.write(f"- Active facts: {stats['active_facts']}\n")
            f.write(f"- Total facts: {stats['total_facts']}\n")
            f.write(f"- MemCells: {stats['total_memcells']}\n")
            f.write(f"- Conflicts: {stats['total_conflicts']}\n")

        print(f"\n{'=' * 70}")
        print(f"  PER-SESSION BENCHMARK COMPLETE")
        print(f"  Results: {md_path}")
        print(f"{'=' * 70}")
        return

    # --- Standard mode ---
    if not args.skip_ingest:
        ingest_conversation(sample)

    if args.skip_benchmark:
        return

    # Determine which modes to run
    if args.fast and not args.normal:
        modes = [("fast", True)]
    elif args.normal and not args.fast:
        modes = [("normal", False)]
    else:
        modes = [("fast", True), ("normal", False)]

    for tag, use_fast in modes:
        print(f"\n{'#' * 70}")
        print(f"  RUNNING MODE: {tag.upper()}")
        print(f"{'#' * 70}")
        results = run_qa(sample, use_fast=use_fast, limit=args.limit,
                         parallel_workers=args.workers)
        print_report_tagged(results, tag)


if __name__ == "__main__":
    main()
