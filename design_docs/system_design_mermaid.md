# System Design — Mermaid Diagrams

## 1. Complete System Architecture

```mermaid
graph TB
    %% Entry Points
    subgraph FRONTEND ["Frontend (React + Vite)"]
        UI_CHAT[Chat Interface]
        UI_QUERY[Query Modal]
    end

    subgraph API ["FastAPI Backend"]
        EP_CHAT["/chat endpoint"]
        EP_QUERY["/query endpoint"]
        EP_STREAM["/chat/stream endpoint"]
    end

    UI_CHAT -->|POST /chat| EP_CHAT
    UI_CHAT -->|POST /chat/stream| EP_STREAM
    UI_QUERY -->|POST /query| EP_QUERY

    %% Chat Flow
    subgraph CHAT_FLOW ["Chat Flow"]
        CF1[Store Message] --> CF2[Fetch Short-term Memory]
        CF2 --> CF3[Retrieve Long-term Memory]
        CF3 --> CF4[Build Chat Prompt]
        CF4 --> CF5[Gemini + Tools]
        CF5 --> CF6[Store Response]
        CF6 --> CF7[Trigger Ingestion Check]
    end

    %% Query Flow
    subgraph QUERY_FLOW ["Query Flow"]
        QF1[Fetch Short-term Memory] --> QF2[Agentic Retrieve]
        QF2 --> QF3[Build Query Prompt]
        QF3 --> QF4[Gemini + Tools]
        QF4 --> QF5[Log to query_logs]
    end

    EP_CHAT --> CHAT_FLOW
    EP_QUERY --> QUERY_FLOW

    %% Background Ingestion
    subgraph BG_INGEST ["Background Ingestion"]
        BG1{Threshold Check}
        BG1 -->|>= 20 msgs| BG2[Spawn Thread]
        BG1 -->|Periodic 10min, >= 4 msgs| BG2
        BG2 --> INGESTION
    end

    CF7 --> BG1

    %% Ingestion Pipeline
    subgraph INGESTION ["Ingestion Pipeline"]
        direction TB
        I1[Segmentation<br/>1 LLM call] --> I2[Parallel Extraction<br/>N LLM calls, 10 parallel]
        I2 --> I3[Batch Embedding<br/>1 API call]
        I3 --> I4[Phase 1: Concurrent Storage<br/>Semaphore=10]
        I4 --> I5[Phase 2: Sequential<br/>Conflict Detection]
        I5 --> I6[Profile Update<br/>1 LLM call]
    end

    %% Retrieval Pipeline
    subgraph RETRIEVAL ["Retrieval Pipeline"]
        direction TB
        R0[Temporal Parser<br/>regex + optional LLM] --> R1[Embed Query<br/>3072-dim]
        R1 --> R2[Hybrid Search<br/>Keyword ∥ Vector]
        R2 --> R3[RRF Fusion<br/>k=60, kw=1.5x]
        R3 --> R4[Fact Dedup<br/>cosine > 0.9]
        R4 --> R5[Scene Scoring<br/>max RRF per scene]
        R5 --> R6[Episode Pooling<br/>top 5 scenes]
        R6 --> R7[Episode Filters<br/>rerank → semantic → stale]
        R1 --> R8[Foresight Filter<br/>parallel]
        R7 --> R9[Context Composition]
        R8 --> R9
        R9 --> R10[Profile]
    end

    CF3 --> RETRIEVAL
    QF2 --> RETRIEVAL

    %% Sufficiency Loop (Query only)
    subgraph SUFFICIENCY ["Agentic Loop (Query only)"]
        S1[Sufficiency Check<br/>LLM] -->|Insufficient| S2[Query Rewrite<br/>2-3 queries]
        S2 -->|Re-retrieve| R2
        S1 -->|Sufficient| DONE[Return Context]
    end

    R10 --> S1

    %% Storage Layer
    subgraph STORAGE ["Dual-Database Storage"]
        subgraph PG ["PostgreSQL"]
            T1[(memscenes)]
            T2[(memcells)]
            T3[(atomic_facts<br/>+ GIN index)]
            T4[(foresight)]
            T5[(conflicts)]
            T6[(user_profile)]
            T7[(chat_messages)]
            T8[(query_logs)]
        end

        subgraph QD ["Qdrant"]
            Q1[(facts<br/>3072-dim cosine)]
            Q2[(scenes<br/>centroids)]
        end
    end

    %% Ingestion writes
    I4 -->|Insert| T1
    I4 -->|Insert| T2
    I4 -->|Insert| T3
    I4 -->|Insert| T4
    I4 -->|Upsert| Q1
    I4 -->|Upsert| Q2
    I5 -->|Deactivate| T3
    I5 -->|Insert| T5
    I6 -->|Upsert| T6

    %% Retrieval reads
    R2 -->|FTS| T3
    R2 -->|Vector| Q1
    R6 -->|Fetch| T2
    R8 -->|Fetch| T4
    R10 -->|Fetch| T6

    %% Chat storage
    CF1 -->|Insert| T7
    CF6 -->|Insert| T7
    QF5 -->|Insert| T8

    %% Styles
    classDef frontend fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
    classDef api fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    classDef pipeline fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef storage fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    classDef llm fill:#fce4ec,stroke:#c62828,stroke-width:2px

    class UI_CHAT,UI_QUERY frontend
    class EP_CHAT,EP_QUERY,EP_STREAM api
    class T1,T2,T3,T4,T5,T6,T7,T8,Q1,Q2 storage
    class I1,I2,I5,I6,R0,S1,S2 llm
```

## 2. Ingestion Pipeline Detail

```mermaid
graph TD
    A[Chat Messages<br/>ingested=FALSE] -->|Threshold: 20 msgs<br/>or Periodic: 10min| B[Background Thread]

    B --> C[Segmentation<br/>1 LLM call]
    C -->|N segments| D[Parallel Extraction<br/>ThreadPoolExecutor max=10]

    D --> D1[Episode Narrative<br/>2-4 sentences, 3rd person]
    D --> D2[Atomic Facts<br/>present-tense, user-only]
    D --> D3[Foresight Signals<br/>valid_from, valid_until]
    D --> D4[Scene Hint<br/>theme_label, summary]

    D1 --> E[Batch Embed Episodes<br/>1 API call → 3072-dim]

    E --> F["Phase 1: Concurrent Storage<br/>asyncio.gather + Semaphore(10)"]

    subgraph PHASE1 ["Phase 1 — Per Segment (Parallel)"]
        F1[Insert MemCell<br/>→ memcell_id] --> F2[Batch-embed Facts<br/>1 API call]
        F2 --> F3[Insert Facts<br/>→ PostgreSQL]
        F3 --> F4[Upsert Vectors<br/>→ Qdrant facts]
        F4 --> F5[Store Episode Embedding<br/>→ memcells.embedding]
        F5 --> F6[Parse & Insert Foresight<br/>IST timestamps]
        F6 --> F7[Embed Foresight<br/>→ foresight.embedding]
        F7 --> F8{Scene Assignment}
        F8 -->|sim ≥ 0.75| F9[Existing Scene<br/>Update summary + centroid]
        F8 -->|sim < 0.75| F10[New Scene<br/>Use scene_hint, no LLM]
    end

    F --> PHASE1

    PHASE1 --> G["Phase 2: Sequential Conflict Detection<br/>1 segment at a time"]

    subgraph PHASE2 ["Phase 2 — Per Segment (Sequential)"]
        G1[Collect facts_with_embeddings] --> G2["Parallel Candidate Search<br/>ThreadPoolExecutor(10)"]

        G2 --> G2A["Vector Search<br/>Qdrant top_k=3, ≥0.75"]
        G2 --> G2B["Keyword Search<br/>PG FTS top_k=3"]
        G2A --> G3[Merge + Dedup Candidates]
        G2B --> G3

        G3 --> G4["Single Batch LLM Call<br/>All pairs for segment"]

        G4 --> G5{Contradiction?}
        G5 -->|Yes| G6["Deactivate Old Fact<br/>is_active=FALSE<br/>superseded_on=date"]
        G5 -->|No| G7[Keep Both Active]
        G6 --> G8[Insert Conflict Record<br/>resolution=recency_wins]
    end

    G --> PHASE2

    PHASE2 --> H[Profile Update<br/>1 LLM call]
    H --> H1["Fetch all is_active=TRUE facts"]
    H1 --> H2["LLM: previous profile + facts"]
    H2 --> H3["Upsert user_profile<br/>{explicit_facts, implicit_traits}"]

    H3 --> I[Mark Messages ingested=TRUE<br/>Invalidate Foresight Cache]

    %% Styles
    classDef llmcall fill:#fce4ec,stroke:#c62828,stroke-width:2px
    classDef dbwrite fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    classDef process fill:#e3f2fd,stroke:#1565c0,stroke-width:2px

    class C,D,G4,H2 llmcall
    class F1,F3,F4,F5,F6,F7,G6,G8,H3,I dbwrite
    class F2,E,G2,G3 process
```

## 3. Retrieval Pipeline Detail

```mermaid
graph TD
    Q[User Query] --> T{Temporal Keywords?}

    T -->|No match| T1[Skip LLM<br/>0.00s]
    T -->|Match: kal, aaj, etc.| T2[LLM Parse<br/>~0.5s]
    T2 --> T3{Query Type}
    T3 -->|Normal| T4[date_filter set<br/>effective_query_time = resolved date]
    T3 -->|Mixed| T5[is_mixed=true<br/>two-pass retrieval]

    T1 --> EMB[Embed Query<br/>3072-dim, reused everywhere]
    T4 --> EMB
    T5 --> EMB

    EMB --> PAR1[Hybrid Search]
    EMB --> PAR2[Foresight Filter<br/>runs in parallel]

    subgraph HYBRID ["Hybrid Search — ThreadPoolExecutor(2)"]
        direction LR
        KW["Keyword Search<br/>PostgreSQL FTS<br/>ts_rank scoring"]
        VS["Vector Search<br/>Qdrant cosine<br/>optional date_filter"]
    end

    PAR1 --> HYBRID
    HYBRID --> RRF["RRF Fusion<br/>score = Σ weight/(k+rank)<br/>k=60, kw=1.5x, vec=1.0x"]

    RRF --> PTF["Post-fusion Filter<br/>conversation_date ≤ query_time<br/>superseded_on > query_time"]

    PTF --> DEDUP["Fact Deduplication<br/>cosine > 0.9 → drop lower"]

    DEDUP --> SCENE["Scene Scoring<br/>fact → memcell → scene<br/>score = max(RRF) per scene"]

    SCENE --> POOL["Episode Pooling<br/>All episodes from top 5 scenes<br/>conversation_date ≤ query_time"]

    subgraph FILTERS ["Episode Filtering Funnel"]
        direction TB
        EF1["Reranking<br/>Score by best child fact RRF<br/>Drop zero-score, cap at 10"]
        EF2["Semantic Filter<br/>cosine(episode, query) ≥ 0.3<br/>min_keep=3"]
        EF3["Staleness Filter<br/>Drop if >50% facts superseded<br/>min_keep=3"]
        EF1 --> EF2 --> EF3
    end

    POOL --> FILTERS

    subgraph FORESIGHT ["Foresight Filtering"]
        direction TB
        FS1["DB: valid_from ≤ now ≤ valid_until<br/>conversation_date ≤ query_time"]
        FS2["Semantic Scoring<br/>cosine(foresight, query)"]
        FS3["Recency Dedup<br/>cosine > 0.7 → keep newest"]
        FS4["Near-dup Dedup<br/>cosine > 0.9 → collapse"]
        FS5["Top 5 by query_sim"]
        FS1 --> FS2 --> FS3 --> FS4 --> FS5
    end

    PAR2 --> FORESIGHT

    FILTERS --> CTX[Context Composition]
    FORESIGHT --> CTX
    PROF["Profile Retrieval<br/>skip if historical"] --> CTX

    subgraph CONTEXT ["Composed Context"]
        direction TB
        C1["=== User Profile ===<br/>explicit_facts + traits"]
        C2["=== Episodes (3-10) ===<br/>dated narratives"]
        C3["=== Foresight (max 5) ===<br/>time-valid signals"]
        C4["=== Facts (max 5) ===<br/>superseded removal"]
    end

    CTX --> CONTEXT

    CONTEXT --> SUFF{Sufficiency Check<br/>query endpoint only}
    SUFF -->|Sufficient| ANS[Build Prompt → Gemini → Response]
    SUFF -->|Insufficient| REWRITE["Query Rewrite<br/>2-3 refined queries"]
    REWRITE -->|Re-retrieve| PAR1

    %% Styles
    classDef llmcall fill:#fce4ec,stroke:#c62828,stroke-width:2px
    classDef filter fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    classDef parallel fill:#e8eaf6,stroke:#283593,stroke-width:2px
    classDef output fill:#e8f5e9,stroke:#388e3c,stroke-width:2px

    class T2,SUFF,REWRITE,ANS llmcall
    class DEDUP,EF1,EF2,EF3,FS1,FS2,FS3,FS4,PTF filter
    class KW,VS,PAR2 parallel
    class CTX,C1,C2,C3,C4 output
```

## 4. Data Model

```mermaid
erDiagram
    memscenes ||--o{ memcells : "1:N scene_id"
    memcells ||--o{ atomic_facts : "1:N memcell_id"
    memcells ||--o{ foresight : "1:N memcell_id"
    atomic_facts ||--o{ conflicts : "old_fact_id"
    atomic_facts ||--o{ conflicts : "new_fact_id"
    chat_threads ||--o{ chat_messages : "1:N thread_id"

    memscenes {
        int id PK
        varchar theme_label
        text summary
        timestamp created_at
        timestamp updated_at
    }

    memcells {
        int id PK
        text episode_text
        text raw_dialogue
        int scene_id FK
        date conversation_date
        float8_arr embedding
        varchar source_id
        timestamp created_at
    }

    atomic_facts {
        int id PK
        int memcell_id FK
        text fact_text
        tsvector fact_tsv "GIN indexed"
        boolean is_active
        date conversation_date
        date superseded_on
        timestamp created_at
    }

    foresight {
        int id PK
        int memcell_id FK
        text description
        timestamp valid_from
        timestamp valid_until
        float8_arr embedding
        timestamp created_at
    }

    conflicts {
        int id PK
        int old_fact_id FK
        int new_fact_id FK
        varchar resolution
        timestamp detected_at
    }

    user_profile {
        int id PK
        jsonb explicit_facts
        jsonb implicit_traits
        timestamp updated_at
    }

    chat_threads {
        varchar id PK
        varchar title
        timestamp created_at
        timestamp updated_at
    }

    chat_messages {
        int id PK
        varchar thread_id FK
        varchar role
        text content
        boolean ingested
        timestamp created_at
    }

    query_logs {
        int id PK
        varchar thread_id
        text query_text
        text response_text
        text memory_context
        jsonb retrieval_metadata
        timestamp query_time
        timestamp created_at
    }
```

## 5. Conflict Detection Flow

```mermaid
graph TD
    A["New Facts from Segment<br/>[fact_1, fact_2, ..., fact_N]"] --> B["For each fact:<br/>Parallel Hybrid Search"]

    subgraph SEARCH ["Candidate Search — ThreadPoolExecutor(10)"]
        S1["Fact 1 → Vector (top_k=3) + Keyword (top_k=3)"]
        S2["Fact 2 → Vector (top_k=3) + Keyword (top_k=3)"]
        SN["Fact N → Vector (top_k=3) + Keyword (top_k=3)"]
    end

    B --> SEARCH
    SEARCH --> C["Merge + Dedup candidates<br/>Batch DB fetch: get_facts_by_ids()"]

    C --> D["Build Pairs<br/>Each new fact × its candidates"]
    D --> E["Single Batch LLM Call<br/>All pairs for segment"]

    E --> F{For each pair}
    F -->|is_contradiction: true| G["Resolution"]
    F -->|is_contradiction: false| H[No action — coexist]

    subgraph RESOLUTION ["Conflict Resolution"]
        G1["atomic_facts:<br/>is_active = FALSE<br/>superseded_on = current_date"]
        G2["conflicts table:<br/>old_fact_id, new_fact_id<br/>resolution = recency_wins"]
    end

    G --> RESOLUTION

    subgraph LLM_RULES ["LLM Decision Rules"]
        direction LR
        R1["CONTRADICTION<br/>• State Replacement<br/>• Binary Opposition<br/>• Plan Cancellation"]
        R2["COEXISTENCE<br/>• Temporal Progression<br/>• Elaboration<br/>• Paraphrasing"]
    end
```

## 6. Scene Clustering

```mermaid
graph TD
    A[New Episode + Embedding] --> B["Search Qdrant scenes<br/>top_k=1"]

    B --> C{Similarity ≥ 0.75?}

    C -->|Yes| D[Assign to Existing Scene]
    D --> D1["Update summary via LLM<br/>(existing episodes + new)"]
    D1 --> D2["Embed new summary<br/>→ updated centroid"]
    D2 --> D3["Upsert centroid<br/>to Qdrant scenes"]

    C -->|No| E[Create New Scene]
    E --> E1["Use scene_hint from extraction<br/>(no LLM call needed)"]
    E1 --> E2["Embed summary<br/>→ initial centroid"]
    E2 --> E3["Insert to Qdrant scenes<br/>+ PostgreSQL memscenes"]

    D --> F["Set memcells.scene_id"]
    E --> F
```

## 7. Timezone Flow

```mermaid
graph LR
    subgraph INGESTION ["Ingestion"]
        I1["Message created_at<br/>(UTC from DB)"] -->|astimezone IST| I2["conversation_date<br/>(IST DATE)"]
        I3["LLM outputs times<br/>(IST context)"] -->|stored as-is| I4["valid_from / valid_until<br/>(IST TIMESTAMP)"]
        I5["Conflict resolution"] -->|current_date IST| I6["superseded_on<br/>(IST DATE)"]
    end

    subgraph RETRIEVAL ["Retrieval"]
        R1["datetime.now(IST)"] --> R2["query_time<br/>(IST, tzinfo stripped)"]
        R2 --> R3["All filters use IST vs IST"]
    end

    subgraph DISPLAY ["Display"]
        D1["created_at (UTC)"] -->|astimezone IST| D2["HH:MM in prompt"]
        D3["conversation_date (IST)"] -->|as-is| D4["date in context"]
        D5["valid_until (IST)"] -->|as-is| D6["date in foresight"]
    end
```
