# Design Document: AI Companion Memory System

## Overview

This system implements a structured memory layer for AI companions, inspired by the [EverMemOS paper](https://arxiv.org/abs/2601.02163). Instead of storing flat conversation logs, it extracts structured memories from conversations, groups them thematically, detects factual conflicts, handles temporal expiry, and retrieves relevant context. The architecture follows an engram-inspired lifecycle

---

## Tech Stack

| Component          | Technology                                | Purpose                                                        |
| ------------------ | ----------------------------------------- | -------------------------------------------------------------- |
| Core Logic         | Python 3.12                               | All extraction, consolidation, and retrieval logic             |
| LLM                | Gemini (`gemini-3-flash-preview`)         | Chat/extraction, conflict checking, sufficiency evaluation     |
| Embeddings         | Gemini (`gemini-embedding-001`, 3072-dim) | Semantic similarity for facts and scene clustering             |
| Structured Storage | PostgreSQL 16 (Docker)                    | MemCells, MemScenes, facts, foresight, conflicts, user profile |
| Vector Search      | Qdrant (Docker)                           | Cosine similarity search over fact and scene embeddings        |

---

## Architecture

The system is organized into two layers that mirror the production EverMemOS architecture:

```
memory_assignment/
├── docker-compose.yml              # PostgreSQL + Qdrant
├── config.py                       # API keys, DB URIs, thresholds
├── models.py                       # Dataclasses: MemCell, MemScene, AtomicFact, etc.
├── db.py                           # PostgreSQL schema + CRUD helpers
├── vector_store.py                 # Qdrant collection setup + search helpers
├── dummy_data.py                   # Staged synthetic conversation generator (4 stages)
├── main.py                         # CLI entrypoint: ingest / query
│
├── memory_layer/                   # Extraction & Consolidation
│   ├── memcell_extractor.py        # Conversation → topical segments
│   ├── episode_extractor.py        # Segment → Episode + Atomic Facts + Foresight + Scene Hint
│   ├── cluster_manager.py          # Incremental MemScene clustering
│   ├── profile_manager.py          # Hybrid conflict detection + resolution
│   ├── profile_extractor.py        # Active facts → User Profile
│   └── prompts/                    # Prompt templates (plain text)
│       ├── narrative_synthesis.txt  # Combined extraction (episode + facts + foresight + scene hint)
│       ├── conflict_detection.txt   # Strict contradiction checking with categorized rules
│       ├── segmentation.txt         # Topical segmentation
│       ├── profile_extraction.txt   # Active facts → profile
│       ├── sufficiency_check.txt    # Retrieval sufficiency evaluation
│       └── query_rewrite.txt        # Query rewriting for insufficient retrieval
│
├── agentic_layer/                  # Retrieval & Orchestration
│   ├── vectorize_service.py        # Gemini embedding wrapper (single + batch)
│   ├── retrieval_utils.py          # ts_rank keyword search, vector search, RRF fusion
│   ├── fetch_mem_service.py        # MemScene-guided retrieval pipeline
│   └── memory_manager.py           # Agentic retrieval with sufficiency checking
│
├── tests/
│   ├── test_scenarios.py           # 3 scripted test scenarios (dietary, health, job)
│   ├── eval_queries.py             # Stage-specific evaluation queries → markdown report
│   └── cumulative_scale_test.py    # Cumulative scale evaluation driver
```

---

## Database Schema

### PostgreSQL Tables

```sql
-- Thematic groupings (created first due to FK dependency)
memscenes (id, theme_label, summary, created_at, updated_at)

-- Core memory unit — one per conversation segment
memcells (id, episode_text, raw_dialogue, created_at, source_id, scene_id → memscenes, conversation_date DATE)

-- Discrete verifiable statements extracted from episodes
atomic_facts (id, memcell_id → memcells, fact_text, fact_tsv TSVECTOR, is_active, created_at, conversation_date DATE, superseded_on DATE)
  -- GIN index on fact_tsv for fast full-text search
  -- conversation_date: when the source conversation happened (from JSON date field, NOT wall-clock insert time)
  -- superseded_on: set when a newer fact contradicts this one (= conversation_date of the superseding fact)

-- Time-bounded forward-looking signals
foresight (id, memcell_id → memcells, description, valid_from, valid_until, created_at)

-- Contradiction log
conflicts (id, old_fact_id → atomic_facts, new_fact_id → atomic_facts, resolution, detected_at)

-- Compact user profile (singleton row)
user_profile (id, explicit_facts JSONB, implicit_traits JSONB, updated_at)
```

### Qdrant Collections

| Collection | Vector Dim | Stores                                                | Purpose              |
| ---------- | ---------- | ----------------------------------------------------- | -------------------- |
| `facts`    | 3072       | Atomic fact embeddings (payload: fact_id, memcell_id) | Semantic fact search |
| `scenes`   | 3072       | MemScene centroid embeddings (payload: memscene_id)   | Scene clustering     |

---

## Phase I: Trace Formation (Memory Extraction)

### How We Detect Topic Boundaries

Conversations often cover multiple topics in a single session. We use Gemini to perform **segmentation** — the full conversation is sent to the LLM with a prompt asking it to identify topical boundaries and output segment indices.

**Why LLM-based segmentation?** Simple approaches like fixed-size windows break mid-topic, and sentence-similarity-based approaches struggle with natural topic drift. Gemini can understand conversational flow and detect genuine topic shifts (e.g., from career talk to health to hobbies).

**Example:**

```
44 messages → 7 segments
  [1] Dietary experiment with continental cuisine and its conclusion
  [2] Professional role and engineering culture at Apple
  [3] Leg injury from football and initial medical treatment
  [4] Career transition and new principal engineer role at Nvidia
  [5] Vacation planning for Tokyo and the change of plans to Paris
  [6] Exploring musical hobbies like guitar and switching to DJing
  [7] Final update on health and full recovery of the leg injury
```

### Combined Narrative Synthesis

For each segment, a **single LLM call** produces all four outputs via the `narrative_synthesis.txt` prompt:

1. **Episode** — A concise third-person narrative (2-4 sentences) with all pronouns resolved to explicit names/entities.

2. **Atomic Facts** — Discrete, self-contained, verifiable statements. Each fact is understandable without surrounding context.

3. **Foresight** — Time-bounded signals with `[valid_from, valid_until]` timestamps:

| Signal Type                   | Inferred Duration                     |
| ----------------------------- | ------------------------------------- |
| Temporary illness (flu, cold) | ~1-2 weeks                            |
| Medication course             | Duration mentioned, or ~1-2 weeks     |
| Travel plans                  | Specific dates if mentioned           |
| New job / move                | Indefinite (`valid_until = null`)     |
| Injury recovery               | Duration from doctor's recommendation |

4. **Scene Hint** — A pre-computed `{theme_label, summary}` for MemScene assignment. If the episode creates a new scene, this hint is used directly, saving an additional LLM call.

**Why combined?** Merging four extractions into one LLM call reduces API calls per segment. The LLM sees the full segment context for all tasks simultaneously, which actually improves coherence (e.g., foresight dates align with facts mentioned in the same episode).

**Example:**

```
Input: "I tore my ACL playing basketball. Doctor says no sports for 2 months
        and I'm on anti-inflammatory meds for 2 weeks."

Episode: "Aru tore his ACL while playing basketball at Arrillaga gym.
          The doctor prescribed anti-inflammatory medication for 2 weeks
          and restricted all sports for 2 months."

Atomic Facts:
  - "Aru tore his ACL playing basketball"
  - "Aru is on anti-inflammatory meds for 2 weeks"
  - "Aru cannot play sports for 2 months"

Foresight:
  - {description: "Aru is on anti-inflammatory meds", valid_from: "2026-02-20", valid_until: "2026-03-06"}
  - {description: "Aru cannot play sports", valid_from: "2026-02-20", valid_until: "2026-04-20"}

Scene Hint:
  - {theme_label: "ACL Injury Recovery", summary: "Aru tore his ACL and is undergoing recovery..."}
```

### Parallel Extraction with Pre-Batched Embeddings

Segments are processed in batches of 10 using `ThreadPoolExecutor`. Within each batch:

1. **Multiple extraction LLM calls** run in parallel (one per segment)
2. **Episode embeddings** are pre-computed in a single `embed_texts()` batch call
3. Storage proceeds via two-phase async with pre-computed embeddings passed through

### Two-Phase Async Storage

Storage uses `asyncio` with a two-phase approach to maximize concurrency while preserving correctness:

**Phase 1 — Concurrent** (all segments in parallel via `run_in_executor`):

1. Stores the **MemCell** (episode + raw dialogue) in PostgreSQL
2. Stores each **Atomic Fact** in PostgreSQL (with auto-generated TSVECTOR for full-text search)
3. **Batch-embeds** all facts in a single Gemini API call and stores embeddings in Qdrant
4. Stores **Foresight** entries in PostgreSQL with parsed dates
5. **Assigns the MemCell to a MemScene** using pre-computed episode embedding + scene hint

**Phase 2 — Sequential** (segments processed in chronological order):

6. Runs **conflict detection** against existing facts — must be sequential so newer facts always supersede older ones

**Why two phases?** Conflict detection requires that facts are inserted in chronological order (newer facts supersede older ones). Running it concurrently causes a race condition where both directions fire and both facts get deactivated. All other operations (DB inserts, embeddings, vector upserts, scene assignment) are stateless and safe to parallelize.

---

## Phase I-b: Consolidation (MemScene Clustering)

### How MemScenes Are Formed

MemScenes are thematic groupings of related MemCells. They form incrementally as new memories arrive:

1. Use the **pre-computed episode embedding** (from batch extraction, no redundant API call)
2. **Search** Qdrant's `scenes` collection for the nearest existing scene centroid
3. **If similarity >= threshold (0.75):** Assign to that scene, update the scene's summary via Gemini, update the centroid embedding
4. **If similarity < threshold:** Create a new MemScene — use the **scene hint** from combined extraction (no extra LLM call), the episode embedding becomes the new centroid

**Scene summary updates** use Gemini to synthesize the existing summary with the new episode, keeping the summary concise and current rather than appending.

---

## Phase II: Conflict Detection + Temporal Awareness

### Hybrid Conflict Detection Strategy

When new atomic facts are extracted, we check for contradictions using a **two-path candidate selection** followed by **batched LLM verification**:

#### Step 1: Hybrid Candidate Selection

| Path                  | Method                                       | What It Catches                                                                                                                 |
| --------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **Vector similarity** | Qdrant cosine search (threshold: 0.65)       | Semantically similar facts (e.g., "is vegetarian" vs "went vegan")                                                              |
| **Keyword search**    | PostgreSQL full-text `ts_rank` on `fact_tsv` | Structurally similar facts with different entities (e.g., "works at SAIL" vs "works at Tesla" — shared verb, different company) |

Candidates from both paths are merged and deduplicated by `fact_id`. Only `is_active = TRUE` facts are considered.

**Why hybrid?** Pure vector search misses contradictions where entity names differ significantly (e.g., "Apple" vs "Nvidia" have low embedding similarity despite both being employers). Keyword search catches these via shared terms like "works at", "lives in", "studying".

#### Step 2: Batched LLM Verification

All candidates for a single fact are checked in **one LLM call** using the `conflict_detection.txt` prompt. The prompt enforces strict categorization:

**Flag as CONTRADICTION (mutually exclusive):**

- State Replacement: "lives in Wilbur Hall" → "moved to University Ave" (can only live in one place)
- Binary Opposition: "torn ACL" → "ACL fully healed" (can't be both)

**Do NOT flag (coexisting):**

- Temporal Progression: "enrolled in ME 131" → "completed ME 131" (natural timeline)
- Elaboration: "bringing food to Yosemite" → "cooking dishes during trip" (more detail)
- Paraphrasing: "runs 3 miles" → "jogs 3 miles every morning" (same meaning)

Each result includes a `reasoning` field citing the category, forcing the LLM to justify its decision.

#### Step 3: Resolution

Default strategy is **recency wins**:

- The **newer fact** remains active
- The **older fact** is marked `is_active = FALSE` and `superseded_on` is set to the conversation date of the newer fact
- **Both facts stay in the database** — nothing is deleted, history is preserved and queryable for its valid time window
- A **conflict record** logs the old fact, new fact, and resolution timestamp

#### Interactive Mode

When run with `--interactive` or `-i`, the system pauses on each detected conflict and offers 4 resolution options:

1. **Keep new fact** (recency wins) — deactivate old
2. **Keep old fact** — deactivate new
3. **Keep both** — both remain active, conflict logged
4. **Provide corrected version** — user types a merged/corrected fact, both originals deactivated

### Temporal Awareness

#### Foresight Filtering

Foresight entries are filtered at query time using SQL:

```sql
WHERE valid_from <= :query_time
  AND (valid_until IS NULL OR valid_until >= :query_time)
```

- **Active foresight** (within validity window) is included in retrieval context
- **Expired foresight** stays in the database as historical record but is excluded from active retrieval
- **Indefinite foresight** (`valid_until = NULL`) remains active until explicitly superseded

#### Temporal Fact Filtering (via `--date`)

The original `is_active` boolean only answers "is this fact current?" — it cannot answer "was this fact true at time X?" When querying "What was Aru eating in Feb?", the system returned both the old vegetarian diet AND the later chicken/fish change, because superseded facts vanish from all queries and future facts appear in all queries.

To solve this, atomic facts and memcells now store `conversation_date` (from the JSON `date` field, not wall-clock insert time) and `superseded_on` (set during conflict resolution). When `--date` is provided at query time, three retrieval channels are filtered:

**1. Facts** — Keyword search uses temporal bounds instead of `is_active`:

```sql
WHERE conversation_date <= :query_time
  AND (superseded_on IS NULL OR superseded_on > :query_time)
  AND fact_tsv @@ plainto_tsquery(...)
```

Vector search results (Qdrant has no temporal fields) are post-filtered by checking `conversation_date` and `superseded_on` from PostgreSQL.

**2. Episodes** — `_pool_episodes()` filters `WHERE conversation_date <= :query_time`. A Feb query never sees March episode narratives.

**3. User profile** — Skipped entirely for historical queries (`query_time < today`). The profile is always rebuilt from current active facts and would leak future information. Episodes and facts already carry the correct information for the queried time period.

Without `--date`, all existing behavior is preserved unchanged (`is_active = TRUE` filtering, all episodes, full profile).

**Example:**

```
Stage 1 (date: 2026-02-01): "Aru is a strict vegetarian"
  → conversation_date = 2026-02-01, superseded_on = NULL

Stage 2 (date: 2026-03-01): "Aru eats chicken and fish" (conflicts with vegetarian)
  → Old fact: superseded_on = 2026-03-01, is_active = FALSE
  → New fact: conversation_date = 2026-03-01, superseded_on = NULL

Query --date 2026-02-15: "What was Aru eating?"
  → "strict vegetarian" included (conv_date Feb 1 ≤ Feb 15, superseded_on Mar 1 > Feb 15)
  → "chicken and fish" excluded (conv_date Mar 1 > Feb 15)
  → Answer: "strict vegetarian"

Query --date 2026-03-15: "What diet does Aru follow?"
  → "strict vegetarian" excluded (superseded_on Mar 1 ≤ Mar 15)
  → "chicken and fish" included (conv_date Mar 1 ≤ Mar 15, no superseded_on)
  → Answer: "chicken and fish"
```

---

## Phase II-b: User Profile Evolution

After each ingestion, the system updates a compact **User Profile** from **active facts only** :

- **Explicit facts**: Verifiable attributes (job, location, diet, relationships)
- **Implicit traits**: Inferred preferences, habits, personality patterns

The profile is rebuilt by querying `atomic_facts WHERE is_active = TRUE` and sending these facts + the previous profile to Gemini. This ensures superseded facts (e.g., "works at SAIL" after switching to Tesla) never appear in the profile.

**Why active facts instead of scene summaries?** Scene summaries are never regenerated when underlying facts get superseded by conflict detection. Using active facts directly ensures the profile always reflects the current ground truth.

---

## Phase III: Smart Retrieval

### Hybrid Search (Keyword + Semantic)

Given a query, the system runs two parallel searches:

| Strategy     | Implementation                                                | Strength                                         |
| ------------ | ------------------------------------------------------------- | ------------------------------------------------ |
| **Keyword**  | PostgreSQL `ts_rank` on `atomic_facts.fact_tsv` (GIN-indexed) | Exact term matching, handles names/entities well |
| **Semantic** | Qdrant cosine similarity on fact embeddings                   | Understands meaning, handles paraphrases         |

Results are merged using **Reciprocal Rank Fusion (RRF)**:

```
score(fact) = Σ 1/(k + rank)    where k = 60
```

Each fact's score is the sum of its reciprocal ranks from both lists. This gives high scores to facts that rank well in either or both systems, without needing to normalize the different scoring scales.

### MemScene-Guided Retrieval Pipeline

Rather than returning isolated facts, the system retrieves coherent context through MemScenes:

1. **RRF hybrid search** → top-K atomic facts (temporally filtered when `--date` is provided)
2. **Map facts → MemScenes**: Each fact belongs to a MemCell, which belongs to a MemScene. Score each scene by its best fact's RRF score
3. **Select top-N MemScenes** → pool their episodes (filtered by `conversation_date <= query_time` when `--date` is provided)
4. **Re-rank episodes** by best child fact score
5. **Filter active foresight** based on query timestamp
6. **Fetch user profile** (skipped for historical queries — profile always reflects current state)
7. **Compose context**: Formatted text block with user profile (if present), relevant episodes, active foresight, and top matching facts

### Agentic Retrieval (Sufficiency Check + Query Rewriting)

The system wraps retrieval in an agentic loop:

1. **Round 1**: Run the full retrieval pipeline, compose context
2. **Sufficiency check**: Send query + context to Gemini — "Is this sufficient to answer the query?"
3. **If insufficient** (and rounds < 2):
   - Gemini generates 2-3 **rewritten queries** using strategies like:
     - Pivot / entity association
     - Temporal calculation
     - Concept expansion
     - Constraint relaxation
   - Re-run retrieval with each rewritten query
   - Merge results (deduplicate by fact_id and memcell_id)
   - Check sufficiency again
4. Return final composed context + metadata (rounds taken, sufficiency status)

**Max rounds = 2** to avoid excessive LLM calls while still catching cases where the initial query phrasing misses relevant memories.

## Tradeoffs Made

### 1. Scene Clustering via Centroid, Not Full Re-clustering

When a new MemCell arrives, we do one cosine search to find the nearest existing scene. If it's close enough (≥ 0.75), the MemCell joins that scene and its centroid is updated. If not, a new scene is created using the pre-computed scene hint — no extra LLM call needed.

Full re-clustering (e.g. k-means) would produce better groupings but requires comparing every memory against every other, which doesn't scale. Since scene count stays small (3–4 scenes even across 500 messages), incremental assignment is fast and good enough.

### 2. Profile from Active Facts vs Scene Summaries

We rebuild the user profile from `is_active = TRUE` facts instead of scene summaries. This guarantees the profile never contains superseded information, at the cost of losing the narrative richness of scene summaries. The tradeoff favors accuracy over eloquence.

### 3. Temporal Post-Filtering Adds Retrieval Latency

When `query_time` is set, vector search results from Qdrant must be post-filtered against PostgreSQL to check `conversation_date` and `superseded_on` bounds. This means each candidate fact triggers a `db.get_fact_by_id()` round-trip, adding latency that scales linearly with the number of candidates. Combined with over-fetching to compensate for filtered-out results, temporal queries are noticeably slower than non-temporal ones (e.g., 23.5s at Stage 3 vs ~14s without temporal filtering).

**Solution:** Store `conversation_date` and `superseded_on` as payload metadata directly in Qdrant. Qdrant natively supports payload-based filtering during search via `Filter` conditions (e.g., `conversation_date <= query_time AND (superseded_on IS NULL OR superseded_on > query_time)`). This would eliminate the post-filter entirely — Qdrant returns only temporally valid facts in a single call, removing both the per-fact DB round-trips and the need to over-fetch. The only additional requirement is updating the Qdrant payload when a fact gets superseded during conflict resolution.

### 4. Full-Conversation Segmentation, Not Windowed

The entire conversation (100–500 messages) is sent to the LLM in a single call for topical segmentation, rather than using a sliding-window approach that processes N messages at a time.

**Why full-conversation?** The LLM sees the complete topical flow and can detect that a "diet" discussion at turn 5 and turn 80 belong to different contexts (baseline vs post-injury), producing accurate segment boundaries. A windowed approach would create artificial breaks at window edges, splitting natural topics mid-conversation. Sending 500 messages in one call pushes against context window limits — quality may degrade on very long inputs as the LLM struggles to attend to all turns equally.

---

## Test Scenarios

### Scripted Functional Tests (`tests/test_scenarios.py`)

Three scripted scenarios using the Aru/Stanford persona, run via `python tests/test_scenarios.py all`

## Scale Evaluation

### Data Generation Strategy

Synthetic conversations are generated via `data/dummy_data.py` using a two-agent chat system (Gemini role-plays both "Aru" and his "friend"). Four stages simulate progressive life changes:

| Stage | Messages | Date       | Key Events                                                                                                                                                     |
| ----- | -------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | 100      | 2026-02-01 | Baseline: Stanford ME junior, Wilbur Hall, strict vegetarian, basketball, guitar, Yosemite trip planned                                                        |
| 2     | 100      | 2026-03-01 | ACL tear (Feb 20), meds 2 weeks, no sports 2 months, diet → chicken/fish, dropped ME 131 → CS 229, moved to University Ave, Yosemite cancelled → Tokyo planned |
| 3     | 100      | 2026-05-01 | ACL healed, SAIL internship, quit guitar → bouldering, Tokyo confirmed for August                                                                              |
| 4     | 200      | 2026-06-15 | Left SAIL → Tesla Autopilot, relocated to Palo Alto, food poisoning + antibiotics 5 days, surfing added                                                        |

Each stage is ingested incrementally (`--no-reset`), building on previous data.

### Scale Results

| Metric                      | Stage 1 (100 msgs) | Stage 2 (+100 msgs) | Stage 3 (+100 msgs) | Stage 4 (+200 msgs) |
| --------------------------- | ------------------ | ------------------- | ------------------- | ------------------- |
| **Query Time**              | 2026-02-01         | 2026-03-01          | 2026-05-01          | 2026-06-15          |
| **MemCells**                | 10                 | 19                  | 29                  | 45                  |
| **MemScenes**               | 1                  | 2                   | 2                   | 3                   |
| **Active Facts**            | 108                | 200                 | 313                 | 445                 |
| **Total Facts**             | 108                | 220                 | 333                 | 467                 |
| **Conflicts Detected**      | 0                  | 20                  | 20                  | 22                  |
| **Deduplication Rate**      | 0.0%               | 9.1%                | 6.0%                | 4.7%                |
| **Eval Queries Sufficient** | 6/6 (100%)         | 8/10 (80%)          | 10/10 (100%)        | 10/10 (100%)        |
| **Avg Retrieval Latency**   | 14.6s              | 18.6s               | 23.5s               | 26.2s               |

### What Each Stage Demonstrates

**Stage 1 — Basic Extraction, Scene Formation, Simple Retrieval:**

- 10 MemCells extracted and organized into 1 MemScene (concurrent scene assignment causes centroid drift — a known tradeoff of parallel Phase 1 storage)
- 108 atomic facts with zero conflicts (clean baseline, no contradicting information)
- All 6 evaluation queries return sufficient results in round 1
- Profile correctly captures: Stanford ME student, Wilbur Hall, strict vegetarian, basketball/guitar/robotics

**Stage 2 — Conflict Detection Working, Deduplication Reducing Storage:**

- 20 conflicts detected (diet change, location change, course change, cancelled plans)
- Dedup rate: 9.1% as superseded facts accumulate (20 of 220 total facts deactivated)
- Diet query correctly returns "chicken and fish" (vegetarian facts superseded)
- Health query correctly returns "torn ACL, on meds, no sports for 2 months"
- Foresight active: meds until March 6, sports restriction until April 20
- 2 queries insufficient (internship query returns empty — expected, as Aru has no internship yet at this stage)

**Stage 3 — Foresight Expiry Handling, Profile Evolution Visible:**

- Query time is 2026-05-01: anti-inflammatory meds expired (ended March 6), sports restriction expired (ended April 20)
- Health query correctly returns "no active injuries, fully healed, 100% health" — foresight expired
- Medication query correctly returns "no current medication"
- Sports query correctly returns "yes, can play sports" — restriction expired
- SAIL internship correctly retrieved, academic progression visible (ME → CS 229 → SAIL)
- All 10 queries sufficient (100%), including the 2 that were insufficient in Stage 2
- Retrieval latency at 23.5s (increase due to larger fact corpus)

**Stage 4 — Scale + Noise Resilience, Continued Conflict Handling:**

- 200 additional messages (500 cumulative) with everyday noise (weather, movies, routine chat)
- MemCells grew from 29 → 45, a 3rd MemScene formed as new themes emerged (surfing, Tesla, Palo Alto)
- 2 new conflicts detected (left SAIL → Tesla, relocated to Palo Alto)
- Dedup rate dropped to 4.7% — most new messages are additive (surfing, food poisoning) rather than contradictory
- Active facts scaled to 445 with all 10 queries sufficient (100%)
- Food poisoning antibiotics correctly reported as completed on June 15 (query date)
- Tesla Autopilot internship correctly supersedes SAIL; full career progression visible (ME → CS 229 → SAIL → Tesla)
- Retrieval latency at 26.2s — scales linearly with fact count

→ More sample queries and results in [result_summary.md](result_summary.md) and detailed analysis in [results](../results/)

---

## Data Flow Summary

### Ingestion Pipeline

```
Raw Conversation
  │
  ▼
Contextual Segmentation (1 Gemini call)
  │  → Segment 1, Segment 2, ..., Segment N
  │
  ▼  [ThreadPoolExecutor — parallel batches of EXTRACTION_BATCH_SIZE=10]
Combined Extraction (1 Gemini call per segment, all segments in batch run concurrently)
  │  → Episode text
  │  → [Atomic Facts]
  │  → [{Foresight with validity windows}]
  │  → {Scene Hint: theme_label + summary}
  │
  ▼
Pre-batch Episode Embeddings (1 Gemini embed call per batch)
  │  → episode_embeddings[0..n]
  │
  ▼  _store_batch_async()
  │
  │  ┌── Phase 1: CONCURRENT (asyncio.gather, bounded by STORAGE_CONCURRENCY semaphore) ──────────┐
  │  │  For each segment in parallel (current_date threaded through):                              │
  │  │    1. Store MemCell (+ conversation_date) → PostgreSQL                                      │
  │  │    2. Batch-embed Atomic Facts (1 Gemini embed call per segment)                           │
  │  │    3. Insert Atomic Facts (+ conversation_date) → PostgreSQL                                │
  │  │    4. Upsert fact vectors → Qdrant                                                          │
  │  │    5. Store Foresight entries (valid_from / valid_until parsed) → PostgreSQL               │
  │  │    6. Assign MemCell to MemScene (pre-computed embedding + scene hint, 0 extra LLM calls)  │
  │  └─────────────────────────────────────────────────────────────────────────────────────────────┘
  │       ↓ asyncio.gather() completes — all segments fully written before Phase 2 starts
  │
  │  ┌── Phase 2: SEQUENTIAL (chronological order: seg 1 → seg 2 → … → seg N) ──────────────────┐
  │  │  For each segment in order:                                                                │
  │  │    Hybrid Conflict Detection (per fact, current_date passed through):                      │
  │  │      Vector search (Qdrant cosine, threshold 0.65)                                        │
  │  │      + Keyword search (PostgreSQL ts_rank on fact_tsv)                                    │
  │  │      → Merged & deduplicated candidates                                                    │
  │  │      → Batched LLM verification (1 Gemini call per fact, conflict_detection.txt)          │
  │  │      → Log to conflicts table, set is_active=FALSE + superseded_on on old facts           │
  │  └─────────────────────────────────────────────────────────────────────────────────────────────┘
  │
  ▼
Update User Profile from Active Facts (1 Gemini call)
```

### Retrieval Pipeline

```
User Query + Timestamp
  │
  ▼  [agentic_retrieve — memory_manager.py]
  │
  ▼  Round 1: retrieve(query, query_time)  [fetch_mem_service.py]
  │
  │   ┌─────────────────────────────────────────────────────────────────┐
  │   │  hybrid_search(query, top_k, query_time)  [retrieval_utils.py] │
  │   │    if query_time:                     │
  │   │    keyword_search  → PostgreSQL ts_rank on fact_tsv             │
  │   │      (+ temporal WHERE clause when query_time set)              │
  │   │    vector_search   → embed query → Qdrant cosine search         │
  │   │    rrf_fusion      → score(fact) = Σ 1/(k+rank), k=60          │
  │   │    if query_time: _temporal_post_filter (check conversation_date│
  │   │      and superseded_on from PostgreSQL), then trim to top_k     │
  │   │    → Top-K Atomic Facts with RRF scores                        │
  │   └─────────────────────────────────────────────────────────────────┘
  │
  ├─ No facts found → return empty context immediately (no LLM calls)
  │
  ▼  Facts found → continue
  │
  ▼  _score_scenes(top_facts)
  │    fact → memcell → scene_id  (via db.get_memcell_by_id)
  │    score each scene by max RRF score across its facts
  │    → Top-N ScoredScenes
  │
  ▼  _pool_episodes(top_n_scene_ids, query_time)
  │    db.get_memcells_by_scene (+ conversation_date <= query_time filter)
  │    → deduplicated episode list
  │
  ▼  _rerank_episodes(episodes, top_facts)
  │    episode.relevance_score = best RRF score of its child facts
  │    → sorted + truncated to top_k
  │
  ▼  filter_active_foresight(query_time)          ← runs after rerank
  │    SQL: valid_from ≤ query_time ≤ valid_until (or valid_until IS NULL)
  │
  ▼  db.get_user_profile()  ← skipped for historical queries (query_time < today)
  │
  ▼  retrieve() returns { episodes, foresight, profile, facts, scenes }
  │
  ▼  compose_context(result)
  │    Sections (in order): User Profile → Episodes → Active Foresight → Top Facts
  │    → context string
  │
  ├─ result["facts"] empty → return { context, is_sufficient=False, rounds=1 }
  │
  ▼  _check_sufficiency(query, context)  [Gemini call]
  │    → { is_sufficient, key_information_found, missing_information, reasoning }
  │
  ├─ Sufficient (or rounds == MAX_RETRIEVAL_ROUNDS=2)
  │    → return { context, is_sufficient, rounds, result }
  │
  └─ Insufficient + rounds < 2
       │
       ▼  _generate_rewrite_queries(original_query, key_info, missing_info)
       │    [Gemini call] → 2-3 targeted rewritten queries
       │
       ▼  for each rewritten query:
       │    retrieve(rewritten_query, query_time)
       │    _merge_retrieval_results(existing, new)
       │      deduplicate episodes by memcell_id, facts by fact_id
       │      keep foresight + profile from latest result
       │
       ▼  compose_context(merged_result)
       │
       ▼  _check_sufficiency(original_query, merged_context)  [Gemini call]
       │
       → return { context, is_sufficient, rounds=2, result }
          (no further retries — MAX_RETRIEVAL_ROUNDS cap reached)
```

---
