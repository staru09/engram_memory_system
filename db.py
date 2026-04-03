import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DB
from models import Foresight, ConflictLog, UserProfile, ChatThread, ChatMessage, QueryLog

_pool = None


def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        _pool = ThreadedConnectionPool(
            minconn=5, maxconn=40,
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
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None


def init_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id              SERIAL PRIMARY KEY,
            profile_text    TEXT NOT NULL DEFAULT '',
            updated_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS foresight (
            id              SERIAL PRIMARY KEY,
            description     TEXT NOT NULL,
            valid_from      DATE,
            valid_until     DATE,
            evidence        TEXT DEFAULT '',
            duration_days   INTEGER,
            is_active       BOOLEAN DEFAULT TRUE,
            created_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS facts (
            id              SERIAL PRIMARY KEY,
            fact_text       TEXT NOT NULL,
            fact_tsv        TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', fact_text)) STORED,
            category        VARCHAR(100),
            conversation_date DATE,
            source_id       VARCHAR(100),
            ingestion_number INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_facts_tsv ON facts USING GIN (fact_tsv);
        CREATE INDEX IF NOT EXISTS idx_facts_date ON facts (conversation_date);
        CREATE INDEX IF NOT EXISTS idx_facts_category ON facts (category);

        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id              SERIAL PRIMARY KEY,
            archive_text    TEXT NOT NULL DEFAULT '',
            recent_text     TEXT NOT NULL DEFAULT '',
            token_count     INTEGER DEFAULT 0,
            updated_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS conflict_log (
            id              SERIAL PRIMARY KEY,
            category        VARCHAR(100) NOT NULL,
            old_value       TEXT NOT NULL,
            new_value       TEXT NOT NULL,
            resolution      VARCHAR(50) DEFAULT 'recency_wins',
            detected_at     TIMESTAMP DEFAULT NOW()
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
    """)
    conn.commit()
    cur.close()
    release_connection(conn)


# ── Facts CRUD ──


def insert_facts_batch(facts: list[dict], source_id: str = None, ingestion_number: int = 0) -> list[int]:
    """Batch insert facts. Each dict: {text, category, date}."""
    if not facts:
        return []
    conn = get_connection()
    cur = conn.cursor()
    ids = []
    for f in facts:
        cur.execute(
            "INSERT INTO facts (fact_text, category, conversation_date, source_id, ingestion_number) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (f["text"], f.get("category", "general"), f.get("date"), source_id, ingestion_number)
        )
        ids.append(cur.fetchone()[0])
    conn.commit()
    cur.close()
    release_connection(conn)
    return ids


def get_all_facts() -> list[dict]:
    """Return all facts for Qdrant rebuild."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, fact_text, category, conversation_date FROM facts ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(r) for r in rows]


def _to_or_query(query: str) -> str:
    """Convert query to OR-separated words for websearch_to_tsquery."""
    words = query.strip().split()
    return " OR ".join(words) if words else query


def keyword_search_facts(query: str, top_k: int = 5, date_filter: dict = None) -> list[dict]:
    """Full-text search on facts using websearch_to_tsquery with OR logic."""
    or_query = _to_or_query(query)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if date_filter:
        cur.execute("""
            SELECT id, fact_text, category, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM facts
            WHERE fact_tsv @@ websearch_to_tsquery('english', %s)
              AND conversation_date BETWEEN %s AND %s
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, or_query, date_filter["date_from"], date_filter["date_to"], top_k))
    else:
        cur.execute("""
            SELECT id, fact_text, category, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM facts
            WHERE fact_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, or_query, top_k))

    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(r) for r in rows]


# ── User Profile ──

def get_user_profile() -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT profile_text FROM user_profile LIMIT 1")
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row[0] if row else ""


def upsert_user_profile(profile_text: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_profile LIMIT 1")
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE user_profile SET profile_text = %s, updated_at = NOW() WHERE id = %s",
            (profile_text, existing[0])
        )
    else:
        cur.execute(
            "INSERT INTO user_profile (profile_text) VALUES (%s)",
            (profile_text,)
        )
    conn.commit()
    cur.close()
    release_connection(conn)


# ── Foresight ──

def insert_foresight(f: Foresight) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO foresight (description, valid_from, valid_until, evidence, duration_days) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (f.description, f.valid_from, f.valid_until, f.evidence, f.duration_days)
    )
    fid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return fid


def get_active_foresight(query_time) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, description, valid_from, valid_until, evidence, created_at
        FROM foresight
        WHERE is_active = TRUE
          AND valid_from <= %s
          AND (valid_until IS NULL OR valid_until >= %s)
        ORDER BY created_at DESC
    """, (query_time, query_time))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(r) for r in rows]


def expire_foresight(current_date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE foresight SET is_active = FALSE WHERE valid_until IS NOT NULL AND valid_until < %s AND is_active = TRUE",
        (current_date,)
    )
    count = cur.rowcount
    conn.commit()
    cur.close()
    release_connection(conn)
    return count


# ── Conflict Log ──

def insert_conflict_log(conflict: ConflictLog) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO conflict_log (category, old_value, new_value, resolution) VALUES (%s, %s, %s, %s) RETURNING id",
        (conflict.category, conflict.old_value, conflict.new_value, conflict.resolution)
    )
    cid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return cid


# ── Conversation Summaries (two-tier: archive + recent) ──

def get_conversation_summary() -> dict:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT archive_text, recent_text, token_count FROM conversation_summaries LIMIT 1")
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    if not row:
        return {"archive_text": "", "recent_text": "", "token_count": 0}
    return dict(row)


def upsert_conversation_summary(archive_text: str, recent_text: str, token_count: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM conversation_summaries LIMIT 1")
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE conversation_summaries SET archive_text = %s, recent_text = %s, token_count = %s, updated_at = NOW() WHERE id = %s",
            (archive_text, recent_text, token_count, row[0])
        )
    else:
        cur.execute(
            "INSERT INTO conversation_summaries (archive_text, recent_text, token_count) VALUES (%s, %s, %s)",
            (archive_text, recent_text, token_count)
        )
    conn.commit()
    cur.close()
    release_connection(conn)


# ── Ingestion Counter ──

_ingestion_count = 0


def get_and_increment_ingestion_count() -> int:
    global _ingestion_count
    _ingestion_count += 1
    return _ingestion_count


# ── System Stats ──

def get_system_stats() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM conflict_log")
    total_conflicts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM foresight WHERE is_active = TRUE")
    active_foresight = cur.fetchone()[0]
    profile = get_user_profile()
    cur.close()
    release_connection(conn)
    return {
        "total_conflicts": total_conflicts,
        "active_foresight": active_foresight,
        "has_profile": bool(profile),
    }


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
    return list(reversed(rows))


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


# ── Query Log ──

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
