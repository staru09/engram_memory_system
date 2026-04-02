# AI Companion with Long-Term Memory

A conversational AI companion ("Ira") that remembers past conversations using a ChatGPT-inspired memory architecture. Two retrieval strategies: rolling summary for chat (fast, lossy), hybrid search on permanent facts for precise queries (lossless). 1-2 LLM calls per ingestion.

## Architecture

```
User <-> React Frontend <-> FastAPI Backend <-> Gemini LLM
                              |
                     PostgreSQL + Qdrant
                              |
                      Memory Pipeline
               (Ingestion + Profile + Summary + Facts)
```

### Memory System

**Short-term memory:** Last 20-100 unprocessed messages from the current thread — immediate conversational context.

**Long-term memory:** 5 components:

| Component | Description | Storage |
|-----------|-------------|---------|
| **User Profile** | Bullet-point timeless identity facts (~30-50 facts) | PostgreSQL |
| **Foresight** | Time-bounded events with `valid_from` / `valid_until`, auto-expired | PostgreSQL |
| **Rolling Summary** | Two-tier: Archive (compressed old) + Recent (date-tagged entries) | PostgreSQL |
| **Facts Table** | Permanent, never compressed — every extracted fact stored forever | PostgreSQL (tsvector + GIN) |
| **Facts Vectors** | Embeddings for semantic search | Qdrant (3072-dim, cosine) |

### Two Retrieval Strategies

**Chat endpoint (`/chat`)** — conversational, general awareness:
```
Rolling Summary + Profile + Foresight + Working Memory
→ No search, just DB reads (~0.01s retrieval)
→ Good for casual conversation
```

**Query endpoint (`/query`)** — precise recall:
```
Hybrid Search (keyword + vector → RRF fusion → top 5 facts)
+ Profile + Foresight
→ Temporal parser for date-filtered queries
→ Good for specific questions about the past
```

---

## Ingestion Pipeline

**1 LLM call typical, 2 every 5th ingestion, +1 when compression triggers.**

```
Conversation (16-20 turns)
       |
  [1] EXTRACT (1 LLM call)
  |   Input: conversation + rolling summary (for coreference)
  |   Output: consolidated facts (category-tagged, dated) + foresight signals
  |
  [2] In parallel:
  |   [2a] Store facts in PostgreSQL (permanent, keyword searchable)
  |   [2b] Embed facts + store in Qdrant (permanent, vector searchable)
  |   [2c] Append facts to rolling summary (date-tagged entries)
  |   [2d] Store foresight + expire old entries
  |
  [3] Every 5th ingestion:
  |   UPDATE PROFILE (1 LLM call)
  |   → Merges new facts into bullet-point profile
  |   → Inline conflict detection (recency wins)
  |
  [4] If rolling summary >= 80% of 10k token budget:
  |   → Recursive compression: LLM(old_archive + oldest_recent) → new_archive
  |   → Archive capped at 20% of budget
  |   → Dates NEVER dropped
  |
  [5] If profile >= 80% of 3k token budget:
      → Compress profile (unlikely with bullet-point format)
```

### Extraction Details
- **1 LLM call** per session — no segmentation step
- Produces **consolidated facts** — dense, self-contained sentences (not atomic fragments)
- Verbatim details prioritized: sign text, painting descriptions, book titles, pet behaviors
- Generic sentiments skipped ("life is a journey", "family is important")
- Prior rolling summary used as context for coreference resolution

### Compression (inspired by MemGPT)
- **Recursive**: `new_archive = LLM(existing_archive + oldest_recent_entries)`
- Never regenerated from scratch — each cycle is additive
- Recurring facts survive compression, one-off old events get condensed
- **80% soft trigger** — compress early with breathing room

### Ingestion Triggers
- **Benchmark**: After each session
- **Production**: After 20 unprocessed messages accumulate
- **Periodic**: Every 10 minutes, checks for threads with 4+ old unprocessed messages
- **Manual**: `POST /threads/{thread_id}/ingest`

---

## Retrieval

### Chat Endpoint — No Search
```
User message comes in
  |
  Read from DB (parallel, ~0.01s):
  |   +-- Rolling summary (archive + recent)
  |   +-- Active foresight (filtered by query_time)
  |   +-- User profile (bullet points)
  |
  Compose context → LLM answers conversationally
```

### Query Endpoint — Hybrid Search
```
User question comes in
  |
  [1] TEMPORAL PARSE + EMBED QUERY (parallel)
  |   Temporal parser: regex pre-filter → LLM date extraction if needed
  |   Embedding: Gemini embedding-001 (3072-dim)
  |
  [2] HYBRID SEARCH (parallel)
  |   Keyword: PostgreSQL tsvector (ts_rank, OR logic)
  |   Vector: Qdrant cosine similarity (+ date filter if temporal)
  |
  [3] RRF FUSION
  |   K=60, keyword weight 1.5, vector weight 1.0
  |   Deduplicate by fact_id → top 5 facts
  |
  [4] Compose: matched facts + foresight + profile → LLM answers
```

---

## Profile Commands

Users can directly edit their profile via chat:

| Command | Example | Effect |
|---------|---------|--------|
| **Remember** | "Remember that I'm allergic to peanuts" | Adds fact to profile |
| **Forget** | "Forget that I work at Google" | Removes matching line from profile |

Also supports Hinglish: "yaad rakh ki..." / "bhool ja ki..."

---

## Data Storage

### PostgreSQL

| Table | Purpose |
|-------|---------|
| `user_profile` | Bullet-point profile text |
| `foresight` | Time-bounded events with validity windows |
| `conversation_summaries` | Two-tier: `archive_text` + `recent_text` + `token_count` |
| `facts` | Permanent fact store with tsvector index for keyword search |
| `conflict_log` | Detected contradictions (category, old/new value, resolution) |
| `ingestion_counter` | Tracks ingestion count for profile update scheduling |
| `chat_threads` | Chat session metadata |
| `chat_messages` | Raw messages with ingestion status |
| `query_logs` | Query + response audit trail |

### Qdrant

| Collection | Purpose |
|------------|---------|
| `facts` | 3072-dim embeddings with payload: fact_text, conversation_date, date_int, category, fact_id |

---

## Project Structure

```
main.py                  # Ingestion pipeline orchestrator
config.py                # Environment config + memory budgets
db.py                    # PostgreSQL operations (connection pooling)
models.py                # Data classes (UserProfile, Foresight, ConflictLog, etc.)
vector_store.py          # Qdrant facts collection (embed, search, rebuild)

memory_layer/
  extractor.py           # Single-call fact + foresight extraction
  profile_extractor.py   # Profile update, summary append, compression
  prompts/
    extraction.txt       # Fact + foresight extraction prompt
    profile_update.txt   # Profile merge + conflict detection prompt
    summary_compression.txt  # Recursive archive compression prompt
    profile_compression.txt  # Profile compression prompt

agentic_layer/
  fetch_mem_service.py   # retrieve_for_chat() + retrieve_for_query() + hybrid search
  temporal_parser.py     # LLM-based date extraction with regex pre-filter
  vectorize_service.py   # Gemini embedding (single + batch)
  profile_commands.py    # Remember/forget direct profile editing

backend/
  app.py                 # FastAPI app, middleware, lifespan
  schemas.py             # Pydantic request/response models
  gemini.py              # Gemini client, time calculator tool
  prompt.py              # Chat + query prompt builders (Hinglish, identity guardrails)
  ingestion.py           # Batched background ingestion (20 msgs/batch)
  routes/
    threads.py           # Thread CRUD + message history
    chat.py              # /chat + /chat/stream (with profile commands)
    query.py             # /query endpoint (hybrid search)
    stats.py             # System statistics
    ingest.py            # Manual ingestion trigger

frontend/                # React + Vite chat UI
```

## Key Features

- **1-2 LLM calls per ingestion** — extract + profile update every 5th (vs 6+ in previous architecture)
- **Two retrieval strategies** — rolling summary for chat, hybrid search for queries
- **Permanent facts** — never compressed, keyword + vector searchable
- **Consolidated facts** — dense sentences, not atomic fragments
- **Two-tier rolling summary** — recursive compression preserves dates and recurring facts
- **Inline conflict detection** — flags genuine contradictions during profile update
- **Foresight with auto-expiry** — time-bounded events expire when `valid_until` passes
- **Profile commands** — "remember this" / "forget this" for direct profile editing
- **Temporal query parsing** — regex pre-filter + LLM date extraction for date-filtered search
- **Streaming chat** — SSE streaming for real-time response display
- **Identity guardrails** — Ira never reveals AI nature or technical internals

---

## Benchmark Results (LoCoMo)

Tested on LoCoMo benchmark — long-term conversational memory evaluation with 152-199 QA pairs across single-hop, temporal, multi-hop, and open-domain categories.

### Rolling Summary Retrieval (chat path)

| Sample | Speakers | Avg Score | >= 4 | >= 3 | Retrieval |
|--------|----------|-----------|------|------|-----------|
| 0 | Caroline & Melanie | 4.57/5 | 86.2% | 92.1% | 0.01s |
| 1 | Jon & Gina | 4.57/5 | 86.4% | 92.6% | 0.01s |
| 2 | John & Maria | 4.63/5 | 90.1% | 94.0% | 0.02s |
| **Average** | | **4.59/5** | **87.6%** | **92.9%** | **0.01s** |

### Hybrid Search Retrieval (query path)

| Top-K | Avg Score | >= 4 | >= 3 | Retrieval |
|-------|-----------|------|------|-----------|
| 5 | 4.25/5 | 75.7% | 85.5% | 3.27s |
| 10 | 4.28/5 | 77.0% | 85.5% | 1.21s |

Hybrid search scores lower on benchmark because the rolling summary contains all information at this scale (~12k tokens). At production scale with months of conversation, the summary will be heavily compressed (lossy) and hybrid search on permanent facts will fill the gaps.

---
