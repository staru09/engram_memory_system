from google import genai
from config import GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)


def embed_text(text: str) -> list[float]:
    """Embed a single text string using Gemini embedding model."""
    result = client.models.embed_content(
        model=GEMINI_EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts in a single API call."""
    if not texts:
        return []
    result = client.models.embed_content(
        model=GEMINI_EMBEDDING_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]
