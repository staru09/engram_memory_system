import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DB
from models import MemCell, AtomicFact, Foresight, MemScene, Conflict, UserProfile, ChatThread, ChatMessage, QueryLog

_pool = None


def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        _pool = ThreadedConnectionPool(
            minconn=5, maxconn=20,
            host=PG_HOST, port=PG_PORT,
            user=PG_USER, password=PG_PASSWORD,
            dbname=PG_DB
        )
    return _pool


def get_connection():
    return _get_pool().getconn()


def release_connection(conn):
    try:
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def close_pool():
    """Close all connections in the pool. Call on server shutdown."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None


def init_schema():
    """Create all tables if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS memscenes (
            id              SERIAL PRIMARY KEY,
            theme_label     VARCHAR(200),
            summary         TEXT,
            created_at      TIMESTAMP DEFAULT NOW(),
            updated_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS memcells (
            id              SERIAL PRIMARY KEY,
            episode_text    TEXT NOT NULL,
            raw_dialogue    TEXT,
            created_at      TIMESTAMP DEFAULT NOW(),
            source_id       VARCHAR(100),
            scene_id        INTEGER REFERENCES memscenes(id),
            conversation_date DATE,
            embedding       FLOAT8[]
        );

        CREATE TABLE IF NOT EXISTS atomic_facts (
            id              SERIAL PRIMARY KEY,
            memcell_id      INTEGER REFERENCES memcells(id),
            fact_text       TEXT NOT NULL,
            fact_tsv        TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', fact_text)) STORED,
            is_active       BOOLEAN DEFAULT TRUE,
            created_at      TIMESTAMP DEFAULT NOW(),
            conversation_date DATE,
            superseded_on   DATE
        );

        CREATE INDEX IF NOT EXISTS idx_facts_tsv ON atomic_facts USING GIN (fact_tsv);

        CREATE TABLE IF NOT EXISTS foresight (
            id              SERIAL PRIMARY KEY,
            memcell_id      INTEGER REFERENCES memcells(id),
            description     TEXT NOT NULL,
            valid_from      TIMESTAMP,
            valid_until     TIMESTAMP,
            created_at      TIMESTAMP DEFAULT NOW(),
            embedding       FLOAT8[]
        );

        CREATE TABLE IF NOT EXISTS conflicts (
            id              SERIAL PRIMARY KEY,
            old_fact_id     INTEGER REFERENCES atomic_facts(id),
            new_fact_id     INTEGER REFERENCES atomic_facts(id),
            resolution      VARCHAR(50) DEFAULT 'recency_wins',
            detected_at     TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS user_profile (
            id              SERIAL PRIMARY KEY,
            explicit_facts  JSONB DEFAULT '[]'::jsonb,
            implicit_traits JSONB DEFAULT '[]'::jsonb,
            updated_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS chat_threads (
            id          VARCHAR(100) PRIMARY KEY,
            title       VARCHAR(200),
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id          SERIAL PRIMARY KEY,
            thread_id   VARCHAR(100) NOT NULL REFERENCES chat_threads(id),
            role        VARCHAR(20) NOT NULL,
            content     TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT NOW(),
            ingested    BOOLEAN DEFAULT FALSE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_thread ON chat_messages (thread_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_unprocessed ON chat_messages (ingested) WHERE ingested = FALSE;

        CREATE TABLE IF NOT EXISTS query_logs (
            id                  SERIAL PRIMARY KEY,
            thread_id           VARCHAR(100),
            query_text          TEXT NOT NULL,
            response_text       TEXT,
            memory_context      TEXT,
            retrieval_metadata  JSONB,
            created_at          TIMESTAMP DEFAULT NOW(),
            query_time          TIMESTAMP
        );

        -- Category column for atomic_facts
        DO $$ BEGIN
            ALTER TABLE atomic_facts ADD COLUMN category VARCHAR(50);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;

        CREATE INDEX IF NOT EXISTS idx_facts_category ON atomic_facts (category) WHERE is_active = TRUE;

        -- Fact updates tracking table
        CREATE TABLE IF NOT EXISTS fact_updates (
            id              SERIAL PRIMARY KEY,
            old_fact_id     INTEGER REFERENCES atomic_facts(id),
            new_fact_id     INTEGER REFERENCES atomic_facts(id),
            update_type     VARCHAR(50),
            created_at      TIMESTAMP DEFAULT NOW()
        );

        -- Conversation summaries (rolling summary for extraction context)
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id              SERIAL PRIMARY KEY,
            summary_text    TEXT NOT NULL DEFAULT '',
            updated_at      TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    release_connection(conn)


# ── MemScene CRUD ──

def insert_memscene(scene: MemScene) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO memscenes (theme_label, summary) VALUES (%s, %s) RETURNING id",
        (scene.theme_label, scene.summary)
    )
    scene_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return scene_id


def update_memscene_summary(scene_id: int, summary: str, theme_label: str = None):
    conn = get_connection()
    cur = conn.cursor()
    if theme_label:
        cur.execute(
            "UPDATE memscenes SET summary = %s, theme_label = %s, updated_at = NOW() WHERE id = %s",
            (summary, theme_label, scene_id)
        )
    else:
        cur.execute(
            "UPDATE memscenes SET summary = %s, updated_at = NOW() WHERE id = %s",
            (summary, scene_id)
        )
    conn.commit()
    cur.close()
    release_connection(conn)


# ── MemCell CRUD ──

def insert_memcell(cell: MemCell) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO memcells (episode_text, raw_dialogue, source_id, scene_id, conversation_date) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (cell.episode_text, cell.raw_dialogue, cell.source_id, cell.scene_id, cell.conversation_date)
    )
    cell_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return cell_id


def update_memcell_scene(memcell_id: int, scene_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE memcells SET scene_id = %s WHERE id = %s",
        (scene_id, memcell_id)
    )
    conn.commit()
    cur.close()
    release_connection(conn)


def get_memcells_by_scene(scene_id: int, query_time=None) -> list[dict]:
    """Fetch memcells by scene — excludes embedding column (used by cluster_manager for summaries)."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if query_time:
        cur.execute(
            "SELECT id, episode_text, raw_dialogue, created_at, source_id, scene_id, conversation_date FROM memcells WHERE scene_id = %s AND conversation_date <= %s ORDER BY created_at",
            (scene_id, query_time)
        )
    else:
        cur.execute("SELECT id, episode_text, raw_dialogue, created_at, source_id, scene_id, conversation_date FROM memcells WHERE scene_id = %s ORDER BY created_at", (scene_id,))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return rows


# ── AtomicFact CRUD ──

def insert_atomic_fact(fact: AtomicFact) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO atomic_facts (memcell_id, fact_text, is_active, conversation_date, category) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (fact.memcell_id, fact.fact_text, fact.is_active, fact.conversation_date, getattr(fact, 'category', None))
    )
    fact_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return fact_id


def deactivate_fact(fact_id: int, superseded_on: str = None):
    conn = get_connection()
    cur = conn.cursor()
    if superseded_on:
        cur.execute(
            "UPDATE atomic_facts SET is_active = FALSE, superseded_on = %s WHERE id = %s",
            (superseded_on, fact_id)
        )
    else:
        cur.execute("UPDATE atomic_facts SET is_active = FALSE WHERE id = %s", (fact_id,))
    conn.commit()
    cur.close()
    release_connection(conn)


def _to_or_query(query: str) -> str:
    """Convert a query string to OR-separated words for websearch_to_tsquery.
    'meri favorite coffee' → 'meri OR favorite OR coffee'
    This ensures any single matching English word returns results,
    critical for Hinglish queries where most words are Hindi."""
    words = query.strip().split()
    return " OR ".join(words) if words else query


def keyword_search_facts(query: str, top_k: int = 10, query_time=None,
                         date_filter: dict = None) -> list[dict]:
    """Full-text search on atomic_facts using websearch_to_tsquery with OR logic.
    Any matching token returns results (not all tokens required).
    ts_rank naturally scores documents with more matching tokens higher.

    Args:
        date_filter: Optional {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}
    """
    or_query = _to_or_query(query)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Debug: show what PostgreSQL parses the OR query into
    cur.execute("SELECT websearch_to_tsquery('english', %s)::text AS parsed", (or_query,))
    parsed = cur.fetchone()
    print(f"    [keyword-debug] or_query='{or_query}' → tsquery='{parsed['parsed']}'")

    if date_filter:
        cur.execute("""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM atomic_facts
            WHERE conversation_date BETWEEN %s AND %s
              AND is_active = TRUE
              AND fact_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, date_filter["date_from"], date_filter["date_to"], or_query, top_k))
    elif query_time:
        cur.execute("""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM atomic_facts
            WHERE conversation_date <= %s
              AND (superseded_on IS NULL OR superseded_on > %s)
              AND fact_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, query_time, query_time, or_query, top_k))
    else:
        cur.execute("""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM atomic_facts
            WHERE is_active = TRUE
              AND fact_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, or_query, top_k))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return rows


# ── Foresight CRUD ──

def insert_foresight(f: Foresight) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO foresight (memcell_id, description, valid_from, valid_until) VALUES (%s, %s, %s, %s) RETURNING id",
        (f.memcell_id, f.description, f.valid_from, f.valid_until)
    )
    fid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return fid


def get_active_foresight(query_time) -> list[dict]:
    """Return foresight valid at the given time, WITHOUT embeddings (fast).

    Args:
        query_time: IST datetime for all comparisons (conversation_date, valid_from, valid_until)
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT f.id, f.memcell_id, f.description, f.valid_from, f.valid_until,
               f.created_at, m.conversation_date AS source_date
        FROM foresight f
        JOIN memcells m ON f.memcell_id = m.id
        WHERE m.conversation_date <= %s
          AND f.valid_from <= %s
          AND (f.valid_until IS NULL OR f.valid_until >= %s)
        ORDER BY m.conversation_date DESC
    """, (query_time, query_time, query_time))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return rows


def get_foresight_embeddings(foresight_ids: list[int]) -> dict:
    """Fetch embeddings for specific foresight IDs. Returns {id: embedding}."""
    if not foresight_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, embedding FROM foresight WHERE id = ANY(%s)", (foresight_ids,))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return {r["id"]: r["embedding"] for r in rows if r.get("embedding")}


def update_foresight_embedding(foresight_id: int, embedding: list[float]):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE foresight SET embedding = %s WHERE id = %s", (embedding, foresight_id))
    conn.commit()
    cur.close()
    release_connection(conn)


def update_memcell_embedding(memcell_id: int, embedding: list[float]):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE memcells SET embedding = %s WHERE id = %s", (embedding, memcell_id))
    conn.commit()
    cur.close()
    release_connection(conn)


# ── Conflict CRUD ──

def get_episode_staleness(memcell_ids: list[int]) -> dict[int, float]:
    """For each memcell, return the fraction of its facts that have been superseded (0.0-1.0)."""
    if not memcell_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT memcell_id,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE is_active = FALSE) AS superseded
        FROM atomic_facts
        WHERE memcell_id = ANY(%s)
        GROUP BY memcell_id
    """, (memcell_ids,))
    result = {}
    for row in cur.fetchall():
        total = row[1]
        superseded = row[2]
        result[row[0]] = superseded / total if total > 0 else 0.0
    cur.close()
    release_connection(conn)
    return result


def get_superseded_map(fact_ids: list[int]) -> dict[int, int]:
    """For a set of fact IDs, return {old_fact_id: new_fact_id} from conflicts table."""
    if not fact_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT old_fact_id, new_fact_id FROM conflicts
        WHERE old_fact_id = ANY(%s)
          AND resolution = 'recency_wins'
    """, (fact_ids,))
    result = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    release_connection(conn)
    return result


def insert_conflict(c: Conflict) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO conflicts (old_fact_id, new_fact_id, resolution) VALUES (%s, %s, %s) RETURNING id",
        (c.old_fact_id, c.new_fact_id, c.resolution)
    )
    cid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return cid


# ── UserProfile CRUD ──

def upsert_user_profile(profile: UserProfile):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_profile LIMIT 1")
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE user_profile SET explicit_facts = %s, implicit_traits = %s, updated_at = NOW() WHERE id = %s",
            (json.dumps(profile.explicit_facts), json.dumps(profile.implicit_traits), existing[0])
        )
    else:
        cur.execute(
            "INSERT INTO user_profile (explicit_facts, implicit_traits) VALUES (%s, %s)",
            (json.dumps(profile.explicit_facts), json.dumps(profile.implicit_traits))
        )
    conn.commit()
    cur.close()
    release_connection(conn)


def get_user_profile() -> UserProfile | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM user_profile LIMIT 1")
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    if not row:
        return None
    return UserProfile(
        id=row["id"],
        explicit_facts=row["explicit_facts"],
        implicit_traits=row["implicit_traits"],
        updated_at=row["updated_at"]
    )


def update_user_profile_facts(facts: list[str]):
    """Update only the explicit_facts field of user profile (incremental update)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_profile LIMIT 1")
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE user_profile SET explicit_facts = %s, updated_at = NOW() WHERE id = %s",
            (json.dumps(facts), existing[0])
        )
    else:
        cur.execute(
            "INSERT INTO user_profile (explicit_facts) VALUES (%s)",
            (json.dumps(facts),)
        )
    conn.commit()
    cur.close()
    release_connection(conn)


def get_fact_by_id(fact_id: int) -> dict | None:
    """Fetch a single fact by ID."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, fact_text, category, is_active, conversation_date FROM atomic_facts WHERE id = %s", (fact_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row


def get_memcell_by_id(memcell_id: int) -> dict | None:
    """Fetch single memcell — excludes embedding."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, episode_text, raw_dialogue, created_at, source_id, scene_id, conversation_date FROM memcells WHERE id = %s", (memcell_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row


def get_memcells_by_ids(memcell_ids: list[int]) -> dict[int, dict]:
    """Batch fetch multiple memcells by ID — excludes embedding (used by scene scoring, only needs scene_id)."""
    if not memcell_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, episode_text, created_at, source_id, scene_id, conversation_date FROM memcells WHERE id = ANY(%s)", (list(memcell_ids),))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return {row["id"]: row for row in rows}


def get_memcells_by_scenes(scene_ids: list[int], query_time=None) -> list[dict]:
    """Batch fetch memcells for multiple scenes — excludes embedding for speed.
    Use get_memcell_embeddings() to fetch embeddings separately if needed."""
    if not scene_ids:
        return []
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if query_time:
        cur.execute(
            "SELECT id, episode_text, raw_dialogue, created_at, source_id, scene_id, conversation_date FROM memcells WHERE scene_id = ANY(%s) AND conversation_date <= %s ORDER BY created_at",
            (list(scene_ids), query_time)
        )
    else:
        cur.execute("SELECT id, episode_text, raw_dialogue, created_at, source_id, scene_id, conversation_date FROM memcells WHERE scene_id = ANY(%s) ORDER BY created_at", (list(scene_ids),))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return rows


def get_memcell_embeddings(memcell_ids: list[int]) -> dict:
    """Fetch embeddings for specific memcell IDs. Returns {id: embedding}."""
    if not memcell_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, embedding FROM memcells WHERE id = ANY(%s) AND embedding IS NOT NULL", (list(memcell_ids),))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return {r["id"]: r["embedding"] for r in rows}


def get_fact_by_id(fact_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM atomic_facts WHERE id = %s", (fact_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row


def get_facts_by_ids(fact_ids: list[int]) -> dict[int, dict]:
    """Batch fetch multiple facts by ID in a single query. Returns {id: row_dict}."""
    if not fact_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM atomic_facts WHERE id = ANY(%s)", (list(fact_ids),))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return {row["id"]: row for row in rows}


def filter_facts_by_time(fact_ids: list[int], query_time) -> set[int]:
    """Return subset of fact_ids that are temporally valid at query_time."""
    if not fact_ids:
        return set()
    query_date = query_time.date() if isinstance(query_time, datetime) else query_time
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM atomic_facts
        WHERE id = ANY(%s)
          AND (conversation_date IS NULL OR conversation_date <= %s)
          AND (superseded_on IS NULL OR superseded_on > %s)
    """, (fact_ids, query_date, query_date))
    valid = {row[0] for row in cur.fetchall()}
    cur.close()
    release_connection(conn)
    return valid


def filter_active_fact_ids(fact_ids: list[int]) -> set[int]:
    """Return subset of fact_ids that are currently active (is_active=TRUE)."""
    if not fact_ids:
        return set()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM atomic_facts WHERE id = ANY(%s) AND is_active = TRUE", (list(fact_ids),))
    active = {row[0] for row in cur.fetchall()}
    cur.close()
    release_connection(conn)
    return active


def insert_fact_update(old_fact_id: int, new_fact_id: int = None, update_type: str = "change") -> int:
    """Record a fact update (UPDATE/DELETE operation). Returns update id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fact_updates (old_fact_id, new_fact_id, update_type) VALUES (%s, %s, %s) RETURNING id",
        (old_fact_id, new_fact_id, update_type)
    )
    update_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return update_id


def get_superseded_fact_ids(query_time) -> set[int]:
    """Return set of fact IDs that are superseded at query_time. Used for fast pre-filtering."""
    query_date = query_time.date() if isinstance(query_time, datetime) else query_time
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM atomic_facts
        WHERE superseded_on IS NOT NULL AND superseded_on <= %s
    """, (query_date,))
    superseded = {row[0] for row in cur.fetchall()}
    cur.close()
    release_connection(conn)
    return superseded


# ── Chat CRUD ──

def get_thread(thread_id: str) -> dict | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM chat_threads WHERE id = %s", (thread_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row


def create_thread(thread_id: str, title: str = None) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_threads (id, title) VALUES (%s, %s) RETURNING id",
        (thread_id, title)
    )
    tid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return tid


def list_threads(limit: int = 20) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT * FROM chat_threads ORDER BY updated_at DESC LIMIT %s", (limit,)
    )
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return rows


def insert_message(thread_id: str, role: str, content: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_messages (thread_id, role, content) VALUES (%s, %s, %s) RETURNING id",
        (thread_id, role, content)
    )
    msg_id = cur.fetchone()[0]
    cur.execute(
        "UPDATE chat_threads SET updated_at = NOW() WHERE id = %s", (thread_id,)
    )
    conn.commit()
    cur.close()
    release_connection(conn)
    return msg_id


def get_thread_messages(thread_id: str, limit: int = 50, before_id: int = None) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if before_id:
        cur.execute(
            "SELECT * FROM chat_messages WHERE thread_id = %s AND id < %s ORDER BY id DESC LIMIT %s",
            (thread_id, before_id, limit)
        )
    else:
        cur.execute(
            "SELECT * FROM chat_messages WHERE thread_id = %s ORDER BY id DESC LIMIT %s",
            (thread_id, limit)
        )
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return list(reversed(rows))  # chronological order


def get_unprocessed_messages(thread_id: str) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT * FROM chat_messages WHERE thread_id = %s AND ingested = FALSE ORDER BY created_at",
        (thread_id,)
    )
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return rows


def mark_messages_ingested(message_ids: list[int]):
    if not message_ids:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE chat_messages SET ingested = TRUE WHERE id = ANY(%s)",
        (message_ids,)
    )
    conn.commit()
    cur.close()
    release_connection(conn)


def get_threads_with_old_unprocessed(minutes: int = 10) -> list[str]:
    """Find threads with unprocessed messages older than the given minutes."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT thread_id FROM chat_messages
        WHERE ingested = FALSE
          AND created_at < NOW() - INTERVAL '%s minutes'
    """, (minutes,))
    thread_ids = [row[0] for row in cur.fetchall()]
    cur.close()
    release_connection(conn)
    return thread_ids


# ── Query Log CRUD ──

def insert_query_log(log: QueryLog) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO query_logs (thread_id, query_text, response_text, memory_context,
                                   retrieval_metadata, query_time)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (log.thread_id, log.query_text, log.response_text, log.memory_context,
         json.dumps(log.retrieval_metadata) if log.retrieval_metadata else None,
         log.query_time)
    )
    log_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return log_id


def get_system_stats() -> dict:
    """Return aggregate counts used by scale_test.py for metrics."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM memcells")
    total_memcells = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM memscenes")
    total_scenes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM conflicts")
    total_conflicts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM atomic_facts WHERE is_active = TRUE")
    active_facts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM atomic_facts")
    total_facts = cur.fetchone()[0]
    cur.close()
    release_connection(conn)
    return {
        "total_memcells": total_memcells,
        "total_scenes": total_scenes,
        "total_conflicts": total_conflicts,
        "active_facts": active_facts,
        "total_facts": total_facts,
    }


# ── Conversation Summaries ──

def get_conversation_summary() -> str | None:
    """Fetch the current rolling conversation summary."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT summary_text FROM conversation_summaries ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row[0] if row else None


def upsert_conversation_summary(summary_text: str):
    """Insert or update the rolling conversation summary."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM conversation_summaries LIMIT 1")
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE conversation_summaries SET summary_text = %s, updated_at = NOW() WHERE id = %s",
            (summary_text, row[0])
        )
    else:
        cur.execute(
            "INSERT INTO conversation_summaries (summary_text) VALUES (%s)",
            (summary_text,)
        )
    conn.commit()
    cur.close()
    release_connection(conn)
