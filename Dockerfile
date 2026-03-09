FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-secret env vars
ENV GEMINI_MODEL=gemini-3-flash-preview
ENV GEMINI_EMBEDDING_MODEL=gemini-embedding-001
ENV PG_HOST=db.jatdhhgawrfdslvkjpcx.supabase.co
ENV PG_PORT=5432
ENV PG_USER=postgres
ENV PG_DB=postgres
ENV QDRANT_URL=https://51ca38e8-3f72-496c-b0d9-f93f57f172af.eu-west-2-0.aws.cloud.qdrant.io
ENV SCENE_SIMILARITY_THRESHOLD=0.75

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
