# AI Companion with Long-Term Memory

A conversational AI companion ("Ira") that remembers past conversations using a structured memory pipeline. It ingests user-assistant dialogues, extracts knowledge (facts, episodes, foresight signals), detects contradictions, and retrieves temporally-aware context for natural Hinglish conversations.

## Architecture

```
User <-> React Frontend <-> FastAPI Backend <-> Gemini LLM
                              |
                    +---------+---------+
                    |                   |
              PostgreSQL (Docker)    Qdrant (Docker)
                    |                   |
                    +---------+---------+
                              |
                     Memory Pipeline
              (Ingestion + Retrieval + Conflict Detection)
```

### Memory System

**Short-term memory:** All unprocessed (not yet ingested) messages from the current thread — provides immediate conversational context.

**Long-term memory:** Structured knowledge extracted via the ingestion pipeline:
- **Atomic Facts** — discrete, verifiable statements with category assignments
- **Episodes** — third-person narrative summaries of conversation segments
- **Foresight Signals** — time-bounded expectations and plans with validity windows
- **User Profile** — synthesized from all active facts (explicit attributes + implicit traits)
- **Session Summaries** — per-session summaries with embeddings
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
  |   -> Atomic facts with category assignment
  |   -> Foresight signals with validity windows
  |   -> Metadata (time_range, entities, topics)
  |   -> Source attribution: only user messages as fact sources
  |   -> [CONTEXT ONLY] assistant lines never used as ground truth
  |
  [4] Batch-embed episodes (1 API call)
  |
  [5] Two-phase storage:
  |   Phase 1 (parallel): Store memcell + facts + foresight
  |   Phase 2 (single call): Conflict detection for all facts combined
  |     -> Current batch facts excluded via Qdrant must_not + SQL WHERE
  |     -> Hybrid candidate search (vector 0.85 threshold + keyword)
  |     -> Chunked LLM contradiction check (max 25 pairs per call, retry on failure)
  |     -> Resolution: recency wins (newer fact supersedes older)
  |
  [6] Post-pipeline (3 tasks in parallel):
      -> Rolling conversation summary rebuild
      -> Per-session summary (stored with embedding)
      -> User profile update (incremental)
```

### Ingestion Triggers
- **Automatic**: After 20 unprocessed messages accumulate
- **Periodic**: Every 10 minutes, checks for threads with 4+ old unprocessed messages
- **Manual**: `POST /threads/{thread_id}/ingest`

---

## Retrieval Pipeline

Two modes:

### Chat Endpoint (fast — profile only)

```
Message
  |
  [1] Fetch user profile (DB read)
  [2] Fetch unprocessed messages (short-term memory)
  [3] Compose context: profile + recent chat
  [4] Stream LLM response via SSE
  |
  Latency: ~0.4s retrieval + LLM
```

### Query Endpoint (normal — full search)

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
  [3] Parallel:
  |   +-- Hybrid search (keyword + vector → RRF fusion)
  |   +-- Foresight filter (validity windows + semantic relevance > 0.7)
  |
  [4] Post-search processing:
  |   -> Cosine dedup (> 0.9)
  |   -> Score filter (< 0.005 dropped)
  |   -> Superseded fact removal
  |
  [5] Episode enrichment (top fact memcell IDs → max 5 episodes)
  |
  [6] Compose context:
  |
  |   === USER PROFILE ===
  |   Known facts + implicit traits
  |
  |   === EPISODES ===
  |   Episode narratives with dates
  |
  |   === FACTS ===
  |   Top-10 atomic facts with dates
  |
  |   === ACTIVE FORESIGHT ===
  |   Foresight signals with validity windows
  |
  [7] LLM answer with time calculator tool
  |
  Latency: ~1-2s retrieval + LLM
```

### Temporal Query Handling

| Query Type | Behavior |
|------------|----------|
| No date reference | Standard search, profile included |
| Historical ("What happened in July?") | Date filter applied, profile excluded |
| Mixed ("Do they still do X?") | Two searches (historical + current), results merged |

---

## Data Storage

### PostgreSQL (Docker)

| Table | Purpose |
|-------|---------|
| `atomic_facts` | Extracted facts with categories, tsvector for keyword search |
| `memcells` | Episode narratives + raw dialogue + embeddings |
| `foresight` | Future-looking signals with validity windows + embeddings |
| `conflicts` | Old/new fact pairs with resolution type |
| `user_profile` | Explicit facts + implicit traits (JSON arrays) |
| `session_summaries` | Per-session summaries with embeddings |
| `conversation_summaries` | Rolling dense summary (singleton) |
| `chat_threads` | Chat session metadata |
| `chat_messages` | Raw messages with ingestion status |
| `query_logs` | Query + response audit trail |

### Qdrant (Docker)

| Collection | Purpose |
|------------|---------|
| `facts` | Fact embeddings (3072-dim Gemini) for semantic search |

---

## Project Structure

```
main.py                  # Ingestion pipeline orchestrator
config.py                # Environment config
db.py                    # PostgreSQL operations (connection pooling)
vector_store.py          # Qdrant vector operations
models.py                # Data classes (MemCell, AtomicFact, Foresight, etc.)

memory_layer/
  memcell_extractor.py   # Segmentation + [CONTEXT ONLY] tagging
  episode_extractor.py   # Extraction + process_segment()
  storage.py             # Two-phase storage (parallel insert + conflict detection)
  profile_manager.py     # Batch conflict detection (hybrid search, chunked LLM)
  profile_extractor.py   # User profile + conversation/session summary builders
  prompts/               # LLM prompt templates

agentic_layer/
  fetch_mem_service.py   # retrieve_simple() + retrieve_fast() + context composition
  retrieval_utils.py     # Hybrid search, foresight caching, deduplication
  vectorize_service.py   # Embedding service (Gemini)
  temporal_parser.py     # Rule-based + LLM temporal expression resolution
  memory_manager.py      # Multi-round agentic retrieval

backend/
  app.py                 # FastAPI app, middleware, lifespan
  schemas.py             # Pydantic request/response models
  gemini.py              # Gemini client, time calculator tool, function calling
  prompt.py              # Chat + query prompt builders (Hinglish, identity guardrails)
  ingestion.py           # Batched background ingestion (20 msgs/batch)
  routes/
    threads.py           # Thread CRUD + message history
    chat.py              # /chat + /chat/stream (profile-only retrieval)
    query.py             # /query (full search pipeline)
    stats.py             # System statistics
    ingest.py            # Manual ingestion trigger

frontend/                # React + Vite chat UI (WhatsApp-style)
```

## Key Features

- **Batched ingestion** — 20 messages per batch, crash-safe (per-batch message marking)
- **Source attribution** — only user messages extracted as facts, assistant is context-only
- **Two retrieval modes** — fast (profile only, ~0.4s) and normal (full pipeline, ~1-2s)
- **Streaming chat** — SSE streaming for real-time response display
- **Temporal awareness** — Hinglish temporal parsing (kal, parso, pehle) with LLM fallback
- **Conflict detection** — batch-excluded candidate search + chunked LLM contradiction check
- **Identity guardrails** — Ira never reveals AI nature or technical internals
- **Per-step timing** — retrieval metadata shows timing breakdown in query modal
- **Coreference resolution** — extraction prompt traces Hinglish pronouns through conversation flow


## Project Demo

[Demo Video](https://www.loom.com/share/dd8f00ca8cb94d158791e0e474e9a0de)
