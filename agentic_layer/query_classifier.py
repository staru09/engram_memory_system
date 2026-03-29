import json
import os
import re
import time
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL
import db

client = genai.Client(api_key=GEMINI_API_KEY)

CLASSIFIER_PROMPT = """You are a query router for a memory retrieval system. Given a user message, classify it and pick relevant categories.

1. Classify the message as NONE, SIMPLE, or COMPLEX.
2. If SIMPLE or COMPLEX, pick the most relevant categories (1-3) from the list.

NONE: The user is sharing information, greeting, making a statement, or chatting casually. They are NOT asking a question that requires looking up past memories.
Examples: "hi", "mera naam rampal hai", "maine aaj pizza khaya", "mera course june me khatam hoga", "theek hai", "haan sahi hai", "accha", "bye"

SIMPLE: The user is asking a general question about preferences, personality, traits, habits, or broad topics. Can be answered from a high-level summary.
Examples: "What does the user like?", "Tell me about yourself", "What are my hobbies?", "Do I have pets?", "mujhe kya pasand hai?"

COMPLEX: The user is asking about specific events, exact dates, verbatim details, sequences, comparisons over time, or needs to search through individual facts.
Examples: "When did I go hiking?", "What book did I recommend?", "What happened on August 19?", "Do I still like coffee?", "kal maine kya khaya tha?"

When in doubt between SIMPLE and COMPLEX, choose COMPLEX.
When in doubt between NONE and a retrieval type, choose NONE — it's better to skip retrieval for a casual message than waste time searching.

Available Categories:
{categories_list}

User message: {query}

Output ONLY valid JSON:
{{"categories": [], "complexity": "NONE" or "SIMPLE" or "COMPLEX"}}"""


USE_LLM_CLASSIFIER = os.environ.get("USE_LLM_CLASSIFIER", "true").lower() == "true"

_QUESTION_SIGNALS = re.compile(
    r'\?|kya\s+.+\s+(hai|tha|the|thi)|kaun|kahan|kab|kb|kaise|kitna|kitne|kitni|'
    r'what|when|where|who|how|which|tell me|bata|btao|batao|yaad|remember',
    re.IGNORECASE
)
_COMPLEX_SIGNALS = re.compile(
    r'kal|parso|pehle|pahle|ago|when|kab|kb|last\s+week|last\s+month|pichle|'
    r'kitna\s+time|how\s+long|still|abhi\s+bhi',
    re.IGNORECASE
)


def _heuristic_classify(query: str) -> dict:
    """Fast keyword-based classification (~0ms). For local testing."""
    t0 = time.time()
    if not _QUESTION_SIGNALS.search(query):
        return {"categories": [], "complexity": "NONE", "classifier_s": round(time.time() - t0, 3)}
    if _COMPLEX_SIGNALS.search(query):
        return {"categories": [], "complexity": "COMPLEX", "classifier_s": round(time.time() - t0, 3)}
    return {"categories": [], "complexity": "SIMPLE", "classifier_s": round(time.time() - t0, 3)}


def classify_query(query: str) -> dict:
    """Classify query as NONE/SIMPLE/COMPLEX and pick relevant categories.

    Uses heuristic (instant) when USE_LLM_CLASSIFIER=false, LLM otherwise.

    Returns:
        {"categories": [...], "complexity": "NONE"|"SIMPLE"|"COMPLEX", "classifier_s": float}
    """
    if not USE_LLM_CLASSIFIER:
        return _heuristic_classify(query)

    t0 = time.time()

    categories = db.get_categories_with_facts()
    if not categories:
        return {"categories": [], "complexity": "NONE", "classifier_s": round(time.time() - t0, 3)}

    categories_list = "\n".join(f"- {cat}" for cat in categories)
    prompt = CLASSIFIER_PROMPT.replace("{categories_list}", categories_list).replace("{query}", query)

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3].strip()

        result = json.loads(text)
        result["classifier_s"] = round(time.time() - t0, 3)
        return result
    except Exception as e:
        print(f"  [classifier] Failed ({e}), defaulting to NONE")
        return {"categories": [], "complexity": "NONE", "classifier_s": round(time.time() - t0, 3)}
