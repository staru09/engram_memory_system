# AI Companion with Long-Term Memory

A conversational AI companion ("Ira") that remembers past conversations using a ChatGPT-inspired memory architecture. No vector search, no RAG — everything is stored in PostgreSQL and included in context every time. 2 LLM calls per ingestion, ~0.01s retrieval.

## Architecture

```
User <-> React Frontend <-> FastAPI Backend <-> Gemini LLM
                              |
                          PostgreSQL
                              |
                     Memory Pipeline
              (Ingestion + Profile + Summary)
```

### Memory System

**Short-term memory:** Last 100-150 unprocessed messages from the current thread — immediate conversational context.

**Long-term memory:** 4 components, all included in every LLM prompt:

| Component | Description | Token Budget |
|-----------|-------------|-------------|
| **User Profile** | Category-tagged paragraphs (personal_info, activities, goals, etc.) with dates where relevant | 6,000 |
| **Foresight** | Time-bounded events with `valid_from` / `valid_until`, auto-expired | — |
| **Rolling Summary (Archive)** | Compressed old events, recurring facts reinforced via recursive compression | ~30% of budget |
| **Rolling Summary (Recent)** | Detailed date-tagged entries from latest sessions | ~70% of budget |
| **Conflict Log** | Tracks contradictions detected during profile updates (category, old/new value, resolution) | — |

---

## Ingestion Pipeline

**2 LLM calls typical, 3-4 when compression triggers.**

```
Conversation (16-20 turns)
       |
  [1] EXTRACT (1 LLM call)
  |   Input: conversation + rolling summary (for coreference)
  |   Output: consolidated facts (category-tagged, dated) + foresight signals
  |
  [2-4] In parallel:
  |   [2] UPDATE PROFILE + detect conflicts (1 LLM call)
  |   |   -> Merges new facts into category sections
  |   |   -> Flags genuine contradictions (not reinforcements)
  |   |   -> Logs conflicts to conflict_log table
  |   |
  |   [3] STORE FORESIGHT + expire old (DB only)
  |   |
  |   [4] APPEND TO ROLLING SUMMARY (no LLM)
  |       -> Format facts as [date] text, append to Recent
  |
  [5] If rolling summary >= 80% token budget:
  |   -> Recursive compression: LLM(old_archive + oldest_recent) -> new_archive
  |   -> Recurring facts reinforced, one-off events condensed
  |   -> Dates NEVER dropped
  |
  [6] If profile >= 80% token budget:
      -> Compress profile, prioritize newer facts over older
```

### Extraction Details
- **1 LLM call** per session — no segmentation step
- Produces **consolidated facts** — dense, self-contained sentences (not atomic fragments)
- Verbatim details prioritized: sign text, painting descriptions, book titles, pet behaviors, emotional reactions
- Generic sentiments skipped ("life is a journey", "family is important")
- Prior rolling summary used as context for coreference resolution

### Compression (inspired by MemGPT)
- **Recursive**: `new_archive = LLM(existing_archive + oldest_recent_entries)`
- Never regenerated from scratch — each cycle is additive
- Recurring facts survive (appear in both archive AND evicted entries)
- One-off old events get condensed
- **80% soft trigger** — compress early with breathing room

### Ingestion Triggers
- **Benchmark**: After each session
- **Production**: After 20 unprocessed messages accumulate
- **Periodic**: Every 10 minutes, checks for threads with 4+ old unprocessed messages
- **Manual**: `POST /threads/{thread_id}/ingest`

---

## Retrieval

**No search.** Just read profile + foresight + rolling summary from DB and include in prompt.

```
Query comes in
  |
  Read from DB (parallel):
  |   +-- user_profile (text)
  |   +-- active foresight (filtered by query_time)
  |   +-- conversation_summary (archive + recent)
  |
  Concatenate into context (~2,700 tokens)
  |
  Include in LLM prompt
  |
  Latency: ~0.01s retrieval + LLM
```

### At Inference Time

```
+-------------------------------------+
|  System Prompt                      |
+-------------------------------------+
|  === USER PROFILE ===               |
|  [personal_info] (2023-06) ...      |
|  [activities] (2023-08) ...         |
|                                     |
|  === UPCOMING / TIME-BOUNDED ===    |
|  - Event X (valid until: date)      |
|                                     |
|  === CONVERSATION HISTORY ===       |
|  [Archive] compressed old events    |
|  [Recent] [date] detailed entries   |
+-------------------------------------+
|  Working Memory (last 100-150 msgs) |
+-------------------------------------+
|  User's new message                 |
+-------------------------------------+

Total context: ~2,700 tokens (0.27% of 1M context limit)
```

---

## Data Storage

### PostgreSQL

| Table | Purpose |
|-------|---------|
| `user_profile` | Category-tagged profile text with dates |
| `foresight` | Time-bounded events with validity windows |
| `conversation_summaries` | Two-tier: `archive_text` + `recent_text` + `token_count` |
| `conflict_log` | Detected contradictions (category, old/new value, resolution) |
| `chat_threads` | Chat session metadata |
| `chat_messages` | Raw messages with ingestion status |
| `query_logs` | Query + response audit trail |

No Qdrant. No vector DB. No embeddings.

---

## Project Structure

```
main.py                  # Ingestion pipeline orchestrator
config.py                # Environment config + memory budgets
db.py                    # PostgreSQL operations (connection pooling)
models.py                # Data classes (UserProfile, Foresight, ConflictLog, etc.)

memory_layer/
  extractor.py           # Single-call fact + foresight extraction
  profile_extractor.py   # Profile update, summary append, compression
  prompts/
    extraction.txt       # Fact + foresight extraction prompt
    profile_update.txt   # Profile merge + conflict detection prompt
    summary_compression.txt  # Recursive archive compression prompt
    profile_compression.txt  # Profile compression prompt

agentic_layer/
  fetch_mem_service.py   # retrieve() + compose_context() — just DB reads

backend/
  app.py                 # FastAPI app, middleware, lifespan
  schemas.py             # Pydantic request/response models
  gemini.py              # Gemini client, time calculator tool
  prompt.py              # Chat + query prompt builders (Hinglish, identity guardrails)
  ingestion.py           # Batched background ingestion (20 msgs/batch)
  routes/
    threads.py           # Thread CRUD + message history
    chat.py              # /chat + /chat/stream
    query.py             # /query endpoint
    stats.py             # System statistics
    ingest.py            # Manual ingestion trigger

frontend/                # React + Vite chat UI
```

## Key Features

- **2 LLM calls per ingestion** — extract + profile update (vs 6+ in previous architecture)
- **No vector search** — everything included in context, ~0.01s retrieval
- **Consolidated facts** — dense sentences, not atomic fragments
- **Two-tier rolling summary** — recursive compression preserves dates and recurring facts
- **Inline conflict detection** — flags genuine contradictions during profile update
- **Foresight with auto-expiry** — time-bounded events expire when `valid_until` passes
- **Source attribution** — only user messages extracted as facts, assistant is context-only
- **Streaming chat** — SSE streaming for real-time response display
- **Identity guardrails** — Ira never reveals AI nature or technical internals

---

## Benchmark Results (LoCoMo)

Tested on LoCoMo benchmark — long-term conversational memory evaluation with 152-199 QA pairs across single-hop, temporal, multi-hop, and open-domain categories.

| Sample | Speakers | Avg Score | >= 4 | >= 3 | Retrieval |
|--------|----------|-----------|------|------|-----------|
| 0 | Caroline & Melanie | 4.49/5 | 84.9% | 90.1% | 0.01s |
| 1 | Jon & Gina | 4.57/5 | 86.4% | 92.6% | 0.01s |
| 2 | John & Maria | 4.63/5 | 90.1% | 94.0% | 0.02s |
| **Average** | | **4.56/5** | **87.1%** | **92.2%** | **0.01s** |

**vs previous RAG architecture:** 4.47/5, 82.2% >= 4, 0.86s retrieval, 6+ LLM calls/session.

---

## Project Demo

[Demo Video](https://www.loom.com/share/dd8f00ca8cb94d158791e0e474e9a0de)
