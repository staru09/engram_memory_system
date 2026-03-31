import time
from google import genai
from config import GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

MAX_RETRIES = 3
RETRY_DELAY = 2


def embed_text(text: str) -> list[float]:
    """Embed a single text string using Gemini embedding model."""
    for attempt in range(MAX_RETRIES):
        try:
            result = client.models.embed_content(
                model=GEMINI_EMBEDDING_MODEL,
                contents=text,
            )
            return result.embeddings[0].values
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  [embed] Retry {attempt + 1}/{MAX_RETRIES}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                raise


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts in a single API call."""
    if not texts:
        return []
    for attempt in range(MAX_RETRIES):
        try:
            result = client.models.embed_content(
                model=GEMINI_EMBEDDING_MODEL,
                contents=texts,
            )
            return [e.values for e in result.embeddings]
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  [embed] Retry {attempt + 1}/{MAX_RETRIES}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                raise
