# Engram — AI Companion with Long-Term Memory

A conversational AI companion ("Ira") that remembers past conversations using a structured memory pipeline. It ingests user-assistant dialogues, extracts knowledge (facts, episodes, foresight signals), detects contradictions, and retrieves temporally-aware context for natural Hinglish conversations.

## Architecture

```
User ↔ React Frontend ↔ FastAPI Backend ↔ Gemini LLM
                              ↓
                    ┌─────────┴─────────┐
                    │                   │
              PostgreSQL (Supabase)  Qdrant (Vector DB)
                    │                   │
                    └─────────┬─────────┘
                              ↓
                     Memory Pipeline
              (Ingestion + Retrieval + Conflict Detection)
```

### Memory System

**Short-term memory:** All unprocessed (not yet ingested) messages from the current thread — provides immediate conversational context.

**Long-term memory:** Structured knowledge extracted via the ingestion pipeline:
- **Atomic Facts** — discrete, verifiable statements (e.g., "Rampal's father's name is Ramesh")
- **Episodes** — third-person narrative summaries of conversation segments
- **MemScenes** — thematic clusters of related episodes
- **Foresight Signals** — time-bounded expectations and plans with validity windows
- **User Profile** — synthesized from all active facts (explicit attributes + implicit traits)
- **Conflict Detection** — identifies and resolves contradictions between new and existing facts

### Retrieval Pipeline

```
Query → Embed → Hybrid Search (vector + keyword RRF) → Scene Scoring
  → Episode Pooling → Foresight Filtering → Context Composition
```

Supports multi-round agentic retrieval with sufficiency checking and query rewriting.

## Backend Structure

```
backend/
├── app.py              # FastAPI app, middleware, lifespan
├── schemas.py          # Pydantic request/response models
├── gemini.py           # Gemini client, time calculator tool, function calling
├── prompt.py           # Chat prompt builder (Hinglish, memory rules, temporal reasoning)
├── ingestion.py        # Background ingestion trigger + periodic checker
└── routes/
    ├── threads.py      # Thread CRUD + message history (cursor-based pagination)
    ├── chat.py         # /chat (with tool calling) + /chat/stream
    ├── stats.py        # System statistics
    └── ingest.py       # Manual ingestion trigger

api.py                  # Backward-compat shim (re-exports backend.app:app)
```

## Core Modules

```
main.py                  # Ingestion pipeline orchestrator + CLI
config.py                # Environment config
db.py                    # PostgreSQL operations (connection pooling)
vector_store.py          # Qdrant vector operations
models.py                # Data classes (MemCell, AtomicFact, Foresight, etc.)

memory_layer/
  memcell_extractor.py   # Conversation segmentation (LLM-based)
  episode_extractor.py   # Episode, facts, foresight extraction (single LLM call per segment)
  cluster_manager.py     # MemScene clustering
  profile_manager.py     # Conflict detection (vector + keyword hybrid, batched)
  profile_extractor.py   # User profile synthesis
  prompts/               # LLM prompt templates

agentic_layer/
  memory_manager.py      # Agentic retrieval (multi-round, sufficiency check)
  fetch_mem_service.py   # Retrieval pipeline orchestration
  retrieval_utils.py     # Hybrid search, foresight caching, deduplication
  vectorize_service.py   # Embedding service (Gemini)

frontend/                # React + Vite chat UI (WhatsApp-style)
```

## Key Features

### Ingestion Pipeline
- Parallel segment extraction via `ThreadPoolExecutor`
- Two-phase storage: concurrent data insertion → sequential conflict detection
- Batched conflict detection (1 LLM call per segment instead of per-fact)
- User-only fact extraction — assistant messages marked `[CONTEXT ONLY]`, never used as source of truth
- Precise timestamps `[HH:MM]` in extraction dialogue for time-aware fact/foresight generation

### Chat System
- Gemini function calling with time calculator tool for precise temporal reasoning
- UTC storage → IST display timezone management throughout
- Connection pooling (`ThreadedConnectionPool`) for PostgreSQL
- Foresight caching (60s TTL, invalidated after ingestion)

### Conflict Detection
- Hybrid candidate search: vector similarity (threshold 0.75) + keyword overlap
- Batched LLM conflict resolution (all candidates per segment in one call)
- Supports superseding old facts with new contradicting ones

## Design Documents

- `design_docs/design_doc.md` — Full architecture and design decisions
- `design_docs/improvement_plan_v4.md` — User-only extraction + timestamp precision plan
- `design_docs/retrieval_v4.md` — Two-stage generation for accuracy (proposed)
- `design_docs/view_only.md` — View-only frontend via Supabase direct connection (proposed)
- `md_files/journey.md` — Engineering journey: 23 stages of iterative development

## Testing

```
tests/
  test_scenarios.py        # Scenario-based tests (dietary, health, job evolution)
  cumulative_scale_test.py # Multi-stage scale test (500 messages)
  eval_queries.py          # Stage-specific evaluation queries
```

## Project Demo

[Demo Video](https://www.loom.com/share/dd8f00ca8cb94d158791e0e474e9a0de)
