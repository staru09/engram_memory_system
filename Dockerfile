FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-secret env vars (secrets set via Cloud Run env vars)
ENV GEMINI_MODEL=gemini-3-flash-preview
ENV GEMINI_EMBEDDING_MODEL=gemini-embedding-001
ENV PG_PORT=5432
ENV PG_USER=postgres
ENV PG_DB=postgres
ENV SCENE_SIMILARITY_THRESHOLD=0.95

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
