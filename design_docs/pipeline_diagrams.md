# Pipeline Architecture Diagrams

## Ingestion Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         INGESTION TRIGGER                               │
│                                                                         │
│   Chat Messages (ingested=FALSE)                                        │
│       │                                                                 │
│       ├── Threshold: >= 20 messages ──┐                                 │
│       └── Periodic: >= 4 msgs, 10min ─┤                                 │
│                                       ▼                                 │
│                          background thread spawned                      │
│                          current_date = last msg UTC → IST              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 1: SEGMENTATION                                    [1 LLM call]   │
│                                                                         │
│   Raw Conversation (N turns)                                            │
│       │                                                                 │
│       ▼                                                                 │
│   extract_segments()                                                    │
│       │  - Groups by micro-events (10-15 turns each)                    │
│       │  - Formats timestamps as IST [HH:MM]                            │
│       │  - Marks assistant msgs as [CONTEXT ONLY]                       │
│       ▼                                                                 │
│   [{segment_id, topic_hint, dialogue, turns}, ...]                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 2: PARALLEL EXTRACTION                    [N LLM calls, 10 ∥]     │
│                                                                         │
│   ThreadPoolExecutor(max_workers=10)                                    │
│       │                                                                 │
│       ├── Segment 1 ──► extract_episode() ──► {episode, facts,          │
│       ├── Segment 2 ──► extract_episode()      foresight, scene_hint}   │
│       ├── Segment 3 ──► extract_episode()                               │
│       └── ...                                                           │
│                                                                         │
│   Each LLM call produces 4 outputs:                                     │
│       ┌──────────────────┐  ┌──────────────────┐                        │
│       │ Episode Narrative│  │ Atomic Facts     │                        │
│       │ (2-4 sentences,  │  │ (present-tense,  │                        │
│       │  3rd person)     │  │  user-only)      │                        │
│       └──────────────────┘  └──────────────────┘                        │
│       ┌──────────────────┐  ┌──────────────────┐                        │
│       │ Foresight Signals│  │ Scene Hint       │                        │
│       │ (valid_from,     │  │ (theme_label,    │                        │
│       │  valid_until)    │  │  summary)        │                        │
│       └──────────────────┘  └──────────────────┘                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 3: BATCH EMBEDDING                              [1 API call]      │
│                                                                         │
│   embed_texts([episode_1, episode_2, ...])                              │
│       │                                                                 │
│       ▼                                                                 │
│   [3072-dim vector, 3072-dim vector, ...]                               │
│   (stored in memcells.embedding, used for scene assignment + retrieval) │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 4: TWO-PHASE ASYNC STORAGE                                        │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  PHASE 1: CONCURRENT (asyncio.gather, Semaphore=10)               │  │
│  │                                                                   │  │
│  │  ┌─── Segment 1 ───┐  ┌─── Segment 2 ───┐  ┌─── Segment N ───┐    │  │
│  │  │                  │  │                  │  │                  │ │  │
│  │  │  4a. Insert      │  │  4a. Insert      │  │  4a. Insert      │ │  │
│  │  │      MemCell     │  │      MemCell     │  │      MemCell     │ │  │
│  │  │      ▼           │  │      ▼           │  │      ▼           │ │  │
│  │  │  4b. Batch-embed │  │  4b. Batch-embed │  │  4b. Batch-embed │ │  │
│  │  │      facts       │  │      facts       │  │      facts       │ │  │
│  │  │      ▼           │  │      ▼           │  │      ▼           │ │  │
│  │  │  4c. Insert facts│  │  4c. Insert facts│  │  4c. Insert facts│ │  │
│  │  │      + Qdrant    │  │      + Qdrant    │  │      + Qdrant    │ │  │
│  │  │      ▼           │  │      ▼           │  │      ▼           │ │  │
│  │  │  4d. Store ep    │  │  4d. Store ep    │  │  4d. Store ep    │ │  │
│  │  │      embedding   │  │      embedding   │  │      embedding   │ │  │
│  │  │      ▼           │  │      ▼           │  │      ▼           │ │  │
│  │  │  4e. Parse &     │  │  4e. Parse &     │  │  4e. Parse &     │ │  │
│  │  │      insert      │  │      insert      │  │      insert      │ │  │
│  │  │      foresight   │  │      foresight   │  │      foresight   │ │  │
│  │  │      ▼           │  │      ▼           │  │      ▼           │ │  │
│  │  │  4f. Assign to   │  │  4f. Assign to   │  │  4f. Assign to   │ │  │
│  │  │      MemScene    │  │      MemScene    │  │      MemScene    │ │  │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                          all Phase 1 complete                           │
│                                    │                                    │
│                                    ▼                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  PHASE 2: SEQUENTIAL CONFLICT DETECTION    [1 LLM call/segment]   │  │
│  │                                                                   │  │
│  │  for each segment (one at a time):                                │  │
│  │                                                                   │  │
│  │  4g. Collect facts_with_embeddings                                │  │
│  │       ▼                                                           │  │
│  │  4h. Parallel candidate search ──────────────────────────┐        │  │
│  │       ThreadPoolExecutor(max_workers=10)                 │        │  │
│  │       │                                                  │        │  │
│  │       ├── Fact 1 ──► Vector (Qdrant, top_k=3, ≥0.75)     │        │  │
│  │       │              Keyword (PG FTS, top_k=3)    ──► merge       │  │
│  │       ├── Fact 2 ──► Vector + Keyword             ──► + dedup     │  │
│  │       └── Fact N ──► Vector + Keyword             ──► candidates  │  │
│  │                                                                   │  │
│  │       ▼                                                           │  │
│  │  4i. Single batch LLM call (all pairs)                            │  │
│  │       │                                                           │  │
│  │       │  CONTRADICTION: State Replacement, Binary Opposition,     │  │
│  │       │                 Plan Cancellation/Rescheduling            │  │
│  │       │  COEXISTENCE:   Temporal Progression, Elaboration,        │  │
│  │       │                 Shifting Logistics, Paraphrasing          │  │
│  │       ▼                                                           │  │
│  │  4j. Resolution (default: recency_wins)                           │  │
│  │       │                                                           │  │
│  │       ├── atomic_facts: is_active=FALSE, superseded_on=date       │  │
│  │       └── conflicts: old_fact_id, new_fact_id, resolution         │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 5: PROFILE UPDATE                                  [1 LLM call]   │
│                                                                         │
│   SELECT fact_text FROM atomic_facts WHERE is_active = TRUE             │
│       │                                                                 │
│       ▼                                                                 │
│   LLM: previous_profile + active_facts                                  │
│       │                                                                 │
│       ▼                                                                 │
│   {explicit_facts: [...], implicit_traits: [...]}                       │
│       │                                                                 │
│       ▼                                                                 │
│   Upsert user_profile table                                             │
└─────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════
                        STORAGE LAYER
═══════════════════════════════════════════════════════════════════════════

  PostgreSQL                              Qdrant
  ┌────────────────────────────┐          ┌────────────────────────────┐
  │ memscenes                  │          │ facts collection           │
  │   theme_label, summary     │          │   id: fact_id              │
  │                            │          │   vector: 3072-dim         │
  │ memcells                   │          │   payload: {fact_id,       │
  │   episode_text, scene_id,  │          │     memcell_id,            │
  │   conversation_date,       │          │     conversation_date}     │
  │   embedding                │          │                            │
  │                            │          │ scenes collection          │
  │ atomic_facts               │          │   id: scene_id             │
  │   fact_text, memcell_id,   │          │   vector: 3072-dim         │
  │   is_active, fact_tsv,     │          │     (centroid)             │
  │   conversation_date,       │          │   payload: {memscene_id}   │
  │   superseded_on            │          │                            │
  │                            │          └────────────────────────────┘
  │ foresight                  │
  │   description, memcell_id, │
  │   valid_from, valid_until, │
  │   embedding                │
  │                            │
  │ conflicts                  │
  │   old_fact_id, new_fact_id,│
  │   resolution               │
  │                            │
  │ user_profile               │
  │   explicit_facts (JSONB),  │
  │   implicit_traits (JSONB)  │
  └────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════
                        SCENE ASSIGNMENT DETAIL
═══════════════════════════════════════════════════════════════════════════

  Episode embedding
       │
       ▼
  Search Qdrant scenes (top_k=1)
       │
       ├── similarity ≥ 0.75 ──► Assign to existing scene
       │                              │
       │                              ├── Update summary (LLM call)
       │                              └── Recompute centroid (embed + upsert)
       │
       └── similarity < 0.75 ──► Create new scene
                                      │
                                      ├── Use scene_hint (no LLM call)
                                      └── Embed summary as initial centroid
```

---

## Retrieval Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          ENTRY POINTS                                   │
│                                                                         │
│  /chat endpoint                        /query endpoint                  │
│  ┌─────────────────────┐              ┌──────────────────────┐          │
│  │ Store message       │              │ (no storage)         │          │
│  │ Fetch short-term    │              │ Fetch short-term     │          │
│  │ retrieve()          │              │ agentic_retrieve()   │          │
│  │ compose_context()   │              │   ├─ retrieve()      │          │
│  │ build_chat_prompt() │              │   ├─ sufficiency     │          │
│  │ Gemini → response   │              │   └─ query rewrite   │          │
│  │ Store response      │              │ build_query_prompt() │          │
│  │ Trigger ingestion   │              │ Gemini → response    │          │
│  └─────────────────────┘              │ Log to query_logs    │          │
│                                       └──────────────────────┘          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 0: TEMPORAL PARSING                          [0-1 LLM call]       │
│                                                                         │
│   Query text                                                            │
│       │                                                                 │
│       ▼                                                                 │
│   Regex pre-filter (_TEMPORAL_KEYWORDS)                                 │
│       │                                                                 │
│       ├── No match ──► return None (0.00s)                              │
│       │     "Aru ko kya pasand hai?"                                    │
│       │                                                                 │
│       └── Match ──► LLM parse (~0.5s)                                   │
│             "kal kya khaya?"                                            │
│                  │                                                      │
│                  ▼                                                      │
│             {date_from, date_to, is_mixed}                              │
│                  │                                                      │
│                  ├── Normal: date_filter set, effective_query_time      │
│                  │          overridden to resolved date                 │
│                  └── Mixed: is_mixed=true, two-pass retrieval           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 1: QUERY EMBEDDING                              [1 API call]      │
│                                                                         │
│   embed_text(query) → 3072-dim vector                                   │
│   (reused by: vector search, episode filter, foresight filter)          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────────┐
                    │                                   │
                    ▼                                   ▼
┌────────────────────────────────────────┐   ┌─────────────────────────┐
│  STEP 2: HYBRID FACT SEARCH            │   │  FORESIGHT (parallel)   │
│                                        │   │                         │
│  ThreadPoolExecutor(max_workers=2)     │   │  Runs alongside Steps   │
│       │                                │   │  2-8 in background      │
│       ├── Keyword Search (PostgreSQL)  │   │                         │
│       │   fact_tsv @@ plainto_tsquery  │   │  See Step 9 below       │
│       │   ts_rank scoring              │   │                         │
│       │                                │   └─────────────────────────┘
│       └── Vector Search (Qdrant)       │
│           cosine similarity            │
│           optional date_filter         │
│                                        │
│  RRF Fusion:                           │
│  score = Σ weight/(k + rank)           │
│  k=60, keyword=1.5×, vector=1.0×       │
│                                        │
│  ┌─────────────────────────────────┐   │
│  │ Post-fusion temporal filter:    │   │
│  │ conversation_date <= query_time │   │
│  │ superseded_on > query_time      │   │
│  └─────────────────────────────────┘   │
│                                        │
│  Mixed queries: two-pass               │
│  ┌──────────────┐  ┌──────────────┐    │
│  │ Pass 1:      │  │ Pass 2:      │    │
│  │ with filter  │  │ no filter    │    │
│  │ (historical) │  │ (current)    │    │
│  └──────┬───────┘  └──────┬───────┘    │
│         └────── merge ─────┘           │
│                                        │
│  Fallback: if < 3 results with filter, │
│  retry without filter                  │
└────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 3: FACT DEDUPLICATION                        [threshold: 0.9]     │
│                                                                         │
│   Batch-fetch embeddings from Qdrant                                    │
│       │                                                                 │
│       ▼                                                                 │
│   Walk facts (highest RRF first):                                       │
│       Fact A (0.042) ──► selected=[] ──► ADD                            │
│       Fact B (0.038) ──► cosine(B,A)=0.95 > 0.9 ──► SKIP                │
│       Fact C (0.031) ──► cosine(C,A)=0.12 < 0.9 ──► ADD                 │
│       ...                                                               │
│   Highest-scored version always wins                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 4: SCENE SCORING                                                  │
│                                                                         │
│   Facts ──► memcell_id ──► scene_id ──► score = max(RRF) per scene      │
│                                                                         │
│   Scene 1: 0.042 (from Fact A)                                          │
│   Scene 3: 0.038 (from Fact B)          Top SCENE_TOP_N=5 selected      │
│   Scene 2: 0.031 (from Fact D)                                          │
│   ...                                                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 5: EPISODE POOLING                                                │
│                                                                         │
│   SELECT * FROM memcells                                                │
│   WHERE scene_id IN (top 5 scenes)                                      │
│     AND conversation_date <= effective_query_time                       │
│                                                                         │
│   All episodes from selected scenes (not just matching ones)            │
│   e.g., 15 episodes from 5 scenes                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEPS 6-8: EPISODE FILTERING                                           │
│                                                                         │
│  15 episodes pooled                                                     │
│       │                                                                 │
│       ▼                                                                 │
│  STEP 6: RERANKING                                                      │
│   Score each episode by best child fact's RRF score                     │
│   Drop zero-score episodes (no matching facts)                          │
│   Cap at RETRIEVAL_TOP_K=10                                             │
│       │                                                                 │
│       ▼  (e.g., 7 episodes)                                             │
│                                                                         │
│  STEP 7: SEMANTIC FILTER                                                │
│   cosine(episode_embedding, query_embedding)                            │
│   Keep if ≥ EPISODE_SIM_THRESHOLD=0.3                                   │
│   Floor: EPISODE_MIN_KEEP=3                                             │
│       │                                                                 │
│       ▼  (e.g., 5 episodes)                                             │
│                                                                         │
│  STEP 8: STALENESS FILTER                                               │
│   staleness = inactive_facts / total_facts per episode                  │
│   Drop if ≥ EPISODE_STALENESS_THRESHOLD=0.5                             │
│   Floor: EPISODE_MIN_KEEP=3                                             │
│       │                                                                 │
│       ▼  (e.g., 4 episodes → context)                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 9: FORESIGHT FILTERING              [ran in parallel]             │
│                                                                         │
│  9a. DB filter (3 conditions):                                          │
│      WHERE conversation_date <= query_time     (source before query)    │
│        AND valid_from <= query_time             (window started)        │
│        AND (valid_until IS NULL                                         │
│             OR valid_until >= query_time)       (not expired)           │
│       │                                                                 │
│       ▼                                                                 │
│  9b. Semantic scoring:                                                  │
│      query_sim = cosine(foresight_emb, query_emb)                       │
│       │                                                                 │
│       ▼                                                                 │
│  9c. Recency dedup (cosine > 0.7):                                      │
│      Same topic, different times → keep newest source_date              │
│      "perfect health" (Feb) dropped, "ACL injury" (Mar) kept            │
│       │                                                                 │
│       ▼                                                                 │
│  9d. Near-duplicate dedup (cosine > 0.9):                               │
│      Almost identical text → keep higher query_sim                      │
│       │                                                                 │
│       ▼                                                                 │
│  9e. Top FORESIGHT_MAX_RESULTS=5 by query_sim                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 10: PROFILE RETRIEVAL                                             │
│                                                                         │
│   is_historical = date_filter AND not mixed                             │
│                   AND resolved_date != today                            │
│       │                                                                 │
│       ├── Historical ──► profile = None (skip)                          │
│       └── Current/Mixed ──► profile = db.get_user_profile()             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 11: CONTEXT COMPOSITION                                           │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ === User Profile ===                              (1 profile)   │    │
│  │ Known facts: ...; ...                                           │    │
│  │ Traits: ...; ...                                                │    │
│  │                                                                 │    │
│  │ === Relevant Memory Episodes ===                  (3-10 eps)    │    │
│  │ [1] (2026-03-14) Episode narrative...                           │    │
│  │ [2] (2026-03-13) Another episode...                             │    │
│  │                                                                 │    │
│  │ === Active Foresight (time-valid) ===             (max 5)       │    │
│  │ - Description (from: ..., valid until: ...)                     │    │
│  │                                                                 │    │
│  │ === Top Matching Facts ===                        (max 5)       │    │
│  │ - Fact text [date] (score: 0.0423)                              │    │
│  │                                                                 │    │
│  │ Extra: superseded facts dropped if replacement in context       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                        ┌───────────┴───────────┐
                        │                       │
                  /chat endpoint          /query endpoint
                        │                       │
                        ▼                       ▼
                 ┌──────────────┐   ┌────────────────────────────────┐
                 │ Skip to      │   │  STEP 12: SUFFICIENCY CHECK    │
                 │ Step 14      │   │                                │
                 │              │   │  LLM: is context sufficient?   │
                 │              │   │  → {is_sufficient, reasoning,  │
                 │              │   │     key_info, missing_info}    │
                 │              │   │                                │
                 │              │   │  If insufficient & round < 2:  │
                 │              │   │                                │
                 │              │   │  STEP 13: QUERY REWRITE        │
                 │              │   │  Generate 2-3 refined queries  │
                 │              │   │  Retrieve with each            │
                 │              │   │  Merge results (dedup by ID)   │
                 │              │   │  Recheck sufficiency           │
                 │              │   │  ──► loop back to Step 12      │
                 │              │   └────────────────────────────────┘
                 │              │                │
                 └──────┬───────┘                │
                        │◄───────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 14: PROMPT CONSTRUCTION                                           │
│                                                                         │
│  /chat: build_chat_prompt()           /query: build_query_prompt()      │
│  ┌──────────────────────────┐        ┌──────────────────────────┐       │
│  │ System: "Ira" Hinglish   │        │ System: "Ira" Hinglish   │       │
│  │ Language rules           │        │ QUERY = user's question  │       │
│  │ Memory rules             │        │ Memory rules             │       │
│  │ Tool instructions        │        │ Recent chat = background │       │
│  │ Current time (IST)       │        │ "Do NOT respond to chat" │       │
│  │ Memory context           │        │ Current time (IST)       │       │
│  │ Recent chat (short-term) │        │ Memory context           │       │
│  └──────────────────────────┘        └──────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 15: ANSWER GENERATION                                             │
│                                                                         │
│   call_gemini_with_tools(prompt)                                        │
│       │                                                                 │
│       ├── Tool loop (max 5 rounds):                                     │
│       │   └── calculate_time_difference(from_time, to_time)             │
│       │       → {total_minutes, hours, minutes, summary}                │
│       │                                                                 │
│       ▼                                                                 │
│   Final response → user                                                 │
│                                                                         │
│   /chat: store response + trigger ingestion                             │
│   /query: log to query_logs (with full metadata)                        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

