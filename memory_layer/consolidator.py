import json
import os
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "consolidation.txt")


def _load_prompt():
    with open(_PROMPT_PATH) as f:
        return f.read()


def consolidate_facts(all_parsed_facts: list[dict], all_fact_ids: list[int]) -> list[dict]:
    """Consolidate atomic facts into grouped, denser entries.
    Returns list of dicts with consolidated_text, fact_ids, metadata."""
    if len(all_parsed_facts) <= 2:
        results = []
        for pf, fid in zip(all_parsed_facts, all_fact_ids):
            results.append({
                "consolidated_text": pf["text"],
                "fact_ids": [fid],
                "metadata": {},
            })
        return results

    facts_text = "\n".join(f"[{i}] {pf['text']}" for i, pf in enumerate(all_parsed_facts))
    prompt = _load_prompt().replace("{facts_with_indices}", facts_text)

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        consolidated = json.loads(text)

        results = []
        for entry in consolidated:
            indices = entry.get("fact_indices", [])
            linked_ids = [all_fact_ids[i] for i in indices if i < len(all_fact_ids)]
            results.append({
                "consolidated_text": entry["consolidated_text"],
                "fact_ids": linked_ids,
                "metadata": entry.get("metadata", {}),
            })
        print(f"  [consolidate] {len(all_parsed_facts)} facts -> {len(results)} consolidated")
        return results
    except Exception as e:
        print(f"  [consolidate] Failed ({e}), wrapping each fact individually")
        results = []
        for pf, fid in zip(all_parsed_facts, all_fact_ids):
            results.append({
                "consolidated_text": pf["text"],
                "fact_ids": [fid],
                "metadata": {},
            })
        return results
