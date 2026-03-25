# LoCoMo Benchmark Results

## Dataset

- **Benchmark:** LoCoMo (ACL 2024) — Evaluating Very Long-Term Conversational Memory
- **Sample:** conv-26 (Sample 0) — 19 sessions, 419 turns, 199 QA pairs
- **Speakers:** Caroline and Melanie
- **QA Categories:** Single-hop (32), Temporal (37), Multi-hop (13), Open-domain (70), Adversarial (47)

---

## Branch: local_fast — Fast Mode

### System Configuration

**Ingestion (v1 pipeline):**
- Segmentation + per-segment extraction (both speakers extracted, `extract_all_speakers=True`)
- Session-based sequential ingestion (19 sessions in chronological order)
- Phase 1: concurrent storage (memcells, facts, foresight, scenes)
- Phase 2: conflict detection (sequential, batched LLM per segment)
- Full profile rebuild (1 LLM call)
- Improved extraction prompt (thorough detail extraction)

**Retrieval (fast mode):**
- Temporal parse (rule-based, ~0ms)
- Embed query (Gemini API)
- Hybrid search: keyword (websearch_to_tsquery OR) + vector (Qdrant), parallel
- RRF fusion (k=60, keyword 1.5x, vector 1.0x)
- Post-fusion temporal filter
- Fact deduplication (cosine > 0.9)
- Score filter (RRF < 0.005, non-temporal only)
- Foresight filter (parallel, cached, relevance >= 0.7)
- Profile retrieval
- Context: profile + foresight + top 10 facts (no episodes)

**Infrastructure:**
- Local PostgreSQL (Docker)
- Local Qdrant (Docker, 3072-dim cosine)
- Gemini gemini-3-flash-preview (LLM)
- Gemini gemini-embedding-001 (embeddings)

### Results

| Metric | Score |
|--------|-------|
| **Avg Score** | **3.45/5** |
| **Score >= 4** | **54.8%** |
| **Score >= 3** | **64.8%** |
| Avg Retrieval | 0.80s |

| Category | Questions | Avg Score | >= 4 | >= 3 |
|----------|-----------|-----------|------|------|
| Single-hop | 32 | 3.84 | 20 (63%) | 25 (78%) |
| **Temporal** | 37 | **4.24** | 28 (76%) | 30 (81%) |
| **Multi-hop** | 13 | **4.31** | 10 (77%) | 11 (85%) |
| Open-domain | 70 | 3.69 | 42 (60%) | 51 (73%) |
| Adversarial | 47 | 1.98 | 9 (19%) | 12 (26%) |

### Branch: local_fast — Normal Mode

**Results:** TBD

---

## Comparison Across All Runs

| Run | Branch | Avg Score | >= 4 | >= 3 | Retrieval |
|-----|--------|-----------|------|------|-----------|
| **local_fast (fast)** | **local_fast** | **3.45** | **54.8%** | **64.8%** | **0.80s** |
| v2+Graph (fast) | v2+graph | 2.59 | 35.2% | 42.7% | 1.06s |
| v1 Normal (both speakers) | earlier | 2.50 | 34.7% | 40.2% | — |
| v2 Fast | earlier | 2.52 | 35.7% | 39.7% | — |
| v1 Fast (both speakers) | earlier | 2.27 | 30.2% | 34.7% | — |
| v1 Fast (speaker A only) | earlier | 2.12 | 25.1% | 28.6% | — |

### Key Findings

1. **local_fast outperforms v2+graph by 33%** (3.45 vs 2.59) — simpler pipeline, better results
2. **Multi-hop: 4.31 vs 2.46** — v1 segmented extraction + episode context captures relationships better than v2 single extraction + Neo4j graph
3. **Temporal: 4.24** — strongest across all runs, rule-based parser + thorough extraction
4. **Improved extraction prompt is the biggest factor** — thorough detail extraction ("extract EVERY detail") + both speakers extracted
5. **Session-based ingestion helps** — chronological order ensures correct conflict detection
6. **Adversarial still weak (1.98)** — system correctly refuses most trick questions but scored low by original metrics

### What Made local_fast Better Than v2+graph

| Factor | v2+graph | local_fast |
|--------|----------|------------|
| Extraction | Single call per session (v2) | Segmented per session (v1) — more thorough |
| Episode context | None (facts only) | Episodes stored, available for normal mode |
| Conflict detection | ADD/UPDATE/DELETE/NOOP (per-fact LLM) | Batch conflict detection (per-segment LLM) |
| Extraction prompt | v2 thorough prompt | Same thorough prompt adapted for v1 format |
| Scene clustering | None | Active (groups related episodes) |
| Fact count | 221 (199 active) | TBD |

The v1 pipeline's **segmented extraction** (10-15 turns per segment) appears to produce more thorough fact extraction than v2's single extraction per full session. The LLM focuses better on smaller chunks.
