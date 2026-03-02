# Engram_inspired_memory

A conversational memory system that ingests user-assistant dialogues, extracts structured knowledge (facts, episodes, foresight signals), detects contradictions, and retrieves temporally-aware context for queries.

## Prerequisites

- Python 3.10+
- Docker & Docker Compose
- Gemini API key

## Setup

### 1. Start databases

```bash
docker-compose up -d
```

This starts PostgreSQL (port 54345) and Qdrant (port 6333).

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set your `GEMINI_API_KEY`. All other defaults work with the Docker setup.

## Usage

### Ingest a conversation

```bash
python main.py ingest <conversation.json> [--reset] [--interactive]
```

- `--reset` — wipe all databases before ingesting
- `--interactive` — prompt for manual conflict resolution

The JSON file should have the format:
```json
{
  "source_id": "conversation_001",
  "date": "2026-02-01",
  "conversation": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

### Query the memory

```bash
python main.py query "What does the user do for work?" [--verbose]
```

- `--verbose` — show full retrieved context before the answer

## Testing

### Scenario tests (dietary conflict, health foresight, job evolution)

```bash
python test/test_scenarios.py all
python test/test_scenarios.py dietary    # single scenario
python test/test_scenarios.py all --verbose  # show pipeline output
```

### Cumulative scale test (500 messages across 4 stages)

```bash
python test/cumulative_scale_test.py
python test/cumulative_scale_test.py --skip-to 3   # resume from stage 3
```

## Generating test data

```bash
python dummy_data.py stage 1        # generate 100 messages for stage 1
python dummy_data.py stage 2 100    # generate 100 messages for stage 2
```

Stage data files go into `scale_data/`.

## Project structure

```
main.py                  # Ingestion + query CLI
config.py                # Environment config
db.py                    # PostgreSQL schema and operations
vector_store.py          # Qdrant vector operations
models.py                # Data classes (MemCell, AtomicFact, Foresight, etc.)
dummy_data.py            # Test data generator (Gemini-powered multi-agent conversations)

memory_layer/
  memcell_extractor.py   # Conversation segmentation (LLM-based)
  episode_extractor.py   # Episode, facts, foresight extraction
  cluster_manager.py     # MemScene clustering
  profile_manager.py     # Conflict detection (vector + keyword hybrid)
  profile_extractor.py   # User profile synthesis

agentic_layer/
  memory_manager.py      # Agentic retrieval (multi-round, sufficiency check)
  fetch_mem_service.py   # Memory fetch orchestration
  retrieval_utils.py     # Hybrid search (RRF fusion of vector + keyword)
  vectorize_service.py   # Embedding service (Gemini)

tests/
  test_scenarios.py      # Scenario-based tests
  cumulative_scale_test.py  # Multi-stage scale test
  eval_queries.py        # Stage-specific evaluation queries

scale_data/              # Generated conversation JSON files per stage
results/                 # Results for scale testing with queries
```
