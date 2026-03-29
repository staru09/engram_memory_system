# AI Companion with Long-Term Memory

A conversational AI companion ("Ira") that remembers past conversations using a structured memory pipeline. It ingests user-assistant dialogues, extracts knowledge (facts, episodes, foresight signals), detects contradictions, and retrieves temporally-aware context for natural Hinglish conversations.

## Architecture

```
User <-> React Frontend <-> FastAPI Backend <-> Gemini LLM
                              |
                    +---------+---------+
                    |                   |
              PostgreSQL (Supabase)  Qdrant (Vector DB)
                    |                   |
                    +---------+---------+
                              |
                     Memory Pipeline
              (Ingestion + Retrieval + Conflict Detection)
```

### Memory System

**Short-term memory:** All unprocessed (not yet ingested) messages from the current thread — provides immediate conversational context.

**Long-term memory:** Structured knowledge extracted via the ingestion pipeline:
- **Atomic Facts** — discrete, verifiable statements with category assignments (e.g., "Rampal's father's name is Ramesh" [personal_info])
- **Episodes** — third-person narrative summaries of conversation segments
- **Foresight Signals** — time-bounded expectations and plans with validity windows
- **User Profile** — synthesized from all active facts (explicit attributes + implicit traits)
- **Category Profiles** — rolling LLM-generated summaries per category (personal_info, preferences, experiences, etc.)
- **Session Summaries** — per-session summaries with embeddings for temporal retrieval
- **Conversation Summary** — rolling dense summary for cross-session reference resolution
- **Conflict Detection** — identifies and resolves contradictions between new and existing facts

---

## Ingestion Pipeline

```
Chat messages (batched, 20 per batch)
  |
  [1] Segmentation (1 LLM call)
  |   -> 1-2 topic-coherent segments per batch
  |   -> Greetings/filler turns ignored
  |   -> Assistant messages tagged [CONTEXT ONLY]
  |
  [2] Fetch rolling conversation summary (DB read)
  |   -> Used for cross-session reference resolution
  |
  [3] Per-segment extraction (N parallel LLM calls)
  |   -> Episode narrative (2-4 sentences)
  |   -> Atomic facts with category assignment (from 10 predefined categories)
  |   -> Foresight signals with validity windows
  |   -> Source attribution: only user messages as fact sources
  |   -> [CONTEXT ONLY] assistant lines never used as ground truth
  |
  [4] Batch-embed episodes (1 API call)
  |
  [5] Two-phase storage:
  |   Phase 1 (parallel): Store memcell + facts + foresight + scene assignment
  |   Phase 2 (sequential): Conflict detection per segment
  |     -> Hybrid candidate search (vector 0.75 threshold + keyword)
  |     -> Batched LLM contradiction check
  |     -> Resolution: recency_wins / keep_old / keep_both
  |
  [6] Post-pipeline (4 tasks in parallel):
      -> Rolling conversation summary rebuild
      -> Per-session summary (stored with embedding)
      -> User profile update
      -> Category profile updates (parallel per category)
```

### Ingestion Triggers
- **Automatic**: After 20 unprocessed messages accumulate
- **Periodic**: Every 10 minutes, checks for threads with 4+ old unprocessed messages
- **Manual**: `POST /threads/{thread_id}/ingest`

---

## Retrieval Pipeline

Two modes available, selected by the user in the Query Modal:

### Fast Mode (profile + category summaries only)

```
Query
  |
  [1] Fetch user profile (DB read)
  [2] Fetch category summaries (DB read)
  [3] Compose context: profile + category summaries
  [4] LLM answer
  |
  Latency: ~0.5s retrieval + LLM
```

### Normal Mode (full search pipeline)

```
Query
  |
  [1] Temporal parse (rule-based, LLM fallback for complex expressions)
  |   -> Hinglish support: kal, parso, pehle, aaj
  |   -> Date range extraction for historical queries
  |   -> Mixed query detection (past + present)
  |
  [2] Embed query (Gemini API)
  |
  [3] Parallel search:
  |   +-- Keyword search (PostgreSQL tsvector GIN index)
  |   +-- Vector search (Qdrant cosine similarity)
  |   +-- Foresight filter (validity windows + semantic relevance)
  |   +-- Category profile fetch
  |
  [4] RRF Fusion (k=60, keyword 1.5x weight, vector 1.0x)
  |   -> Temporal filter (exclude facts outside date range)
  |   -> Cosine dedup (> 0.9)
  |   -> Score filter (< 0.005 dropped)
  |   -> Superseded fact removal
  |
  [5] Episode enrichment (from top fact memcell IDs)
  |
  [6] Compose context (hierarchical):
  |
  |   === HIGH-LEVEL CONTEXT ===
  |   User profile (explicit facts + implicit traits)
  |   Category summaries (matched categories)
  |
  |   === DETAILED EVIDENCE ===
  |   Episodes with dates
  |   Top-10 facts with dates and RRF scores
  |   Active foresight signals
  |
  [7] LLM answer with time calculator tool
  |
  Latency: ~2-4s retrieval + LLM
```

### Chat Endpoint
- Always uses fast mode (profile + category summaries)
- Recent unprocessed messages provided as short-term context
- Streaming responses via SSE (`/chat/stream`)

### Temporal Query Handling

| Query Type | Behavior |
|------------|----------|
| No date reference | Standard search, profile included |
| Historical ("What happened in July?") | Date filter applied, profile excluded |
| Mixed ("Do they still do X?") | Two searches (historical + current), results merged |

---

## Data Storage

### PostgreSQL (Supabase)

| Table | Purpose |
|-------|---------|
| `atomic_facts` | Extracted facts with categories, tsvector for keyword search |
| `memcells` | Episode narratives + raw dialogue + embeddings |
| `memscenes` | Theme clusters (stored, not used in retrieval) |
| `foresight` | Future-looking signals with validity windows + embeddings |
| `conflicts` | Old/new fact pairs with resolution type |
| `user_profile` | Explicit facts + implicit traits (JSON arrays) |
| `profile_categories` | Per-category rolling summaries + embeddings |
| `session_summaries` | Per-session summaries with embeddings |
| `conversation_summaries` | Rolling dense summary (singleton) |
| `chat_threads` | Chat session metadata |
| `chat_messages` | Raw messages with ingestion status |
| `query_logs` | Query + response audit trail |

### Qdrant (Vector DB)

| Collection | Purpose |
|------------|---------|
| `facts` | Fact embeddings (768-dim Gemini) for semantic search |
| `scenes` | Scene embeddings (stored, not used in retrieval) |

---

## Backend Structure

```
backend/
  app.py              # FastAPI app, middleware, lifespan, cache pre-warming
  schemas.py          # Pydantic request/response models
  gemini.py           # Gemini client, time calculator tool, function calling
  prompt.py           # Chat + query prompt builders (Hinglish, identity guardrails)
  ingestion.py        # Batched background ingestion (20 msgs/batch)
  routes/
    threads.py        # Thread CRUD + message history
    chat.py           # /chat + /chat/stream (always fast retrieval)
    query.py          # /query (fast or normal, user selects)
    stats.py          # System statistics
    ingest.py         # Manual ingestion trigger

api.py                # Entry point (re-exports backend.app:app)
```

## Core Modules

```
main.py                  # Ingestion pipeline orchestrator
config.py                # Environment config
db.py                    # PostgreSQL operations (connection pooling)
vector_store.py          # Qdrant vector operations
models.py                # Data classes (MemCell, AtomicFact, Foresight, etc.)

memory_layer/
  memcell_extractor.py   # Segmentation + [CONTEXT ONLY] tagging
  episode_extractor.py   # Extraction with retry on JSON parse failure
  cluster_manager.py     # MemScene clustering (best-effort)
  profile_manager.py     # Conflict detection (hybrid search, batched LLM)
  profile_extractor.py   # User profile + category profile synthesis (parallel)
  prompts/               # LLM prompt templates

agentic_layer/
  fetch_mem_service.py   # retrieve_simple() + retrieve_fast() + context composition
  retrieval_utils.py     # Hybrid search, foresight caching, deduplication
  vectorize_service.py   # Embedding service (Gemini)
  temporal_parser.py     # Rule-based + LLM temporal expression resolution

frontend/                # React + Vite chat UI (WhatsApp-style)
```

## Key Features

- **Batched ingestion** — 20 messages per batch, crash-safe (per-batch message marking)
- **Source attribution** — only user messages extracted as facts, assistant is context-only
- **Two retrieval modes** — fast (profile + summaries, ~0.5s) and normal (full pipeline, ~2-4s)
- **Streaming chat** — SSE streaming for real-time response display
- **Temporal awareness** — Hinglish temporal parsing (kal, parso, pehle) with LLM fallback
- **Conflict detection** — hybrid candidate search + batched LLM contradiction resolution
- **Category profiles** — 10 predefined categories with rolling LLM-generated summaries
- **Identity guardrails** — Ira never reveals AI nature or technical internals
- **Per-step timing** — retrieval metadata shows timing breakdown in query modal


## Project Demo

[Demo Video](https://www.loom.com/share/dd8f00ca8cb94d158791e0e474e9a0de)
