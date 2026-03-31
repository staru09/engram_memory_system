import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DB
from models import MemCell, AtomicFact, Foresight, Conflict, UserProfile, ChatThread, ChatMessage, QueryLog, ProfileCategory, ConsolidatedFact

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
        CREATE TABLE IF NOT EXISTS memcells (
            id              SERIAL PRIMARY KEY,
            episode_text    TEXT NOT NULL,
            raw_dialogue    TEXT,
            created_at      TIMESTAMP DEFAULT NOW(),
            source_id       VARCHAR(100),
            conversation_date DATE,
            embedding       FLOAT8[]
        );

        CREATE TABLE IF NOT EXISTS atomic_facts (
            id              SERIAL PRIMARY KEY,
            memcell_id      INTEGER REFERENCES memcells(id),
            fact_text       TEXT NOT NULL,
            fact_tsv        TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', fact_text)) STORED,
            category_name   VARCHAR(100) DEFAULT 'general',
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

        CREATE TABLE IF NOT EXISTS profile_categories (
            id              SERIAL PRIMARY KEY,
            category_name   VARCHAR(100) NOT NULL UNIQUE,
            description     TEXT DEFAULT '',
            summary_text    TEXT NOT NULL DEFAULT '',
            fact_count      INTEGER DEFAULT 0,
            embedding       FLOAT8[],
            created_at      TIMESTAMP DEFAULT NOW(),
            updated_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id              SERIAL PRIMARY KEY,
            summary_text    TEXT NOT NULL DEFAULT '',
            updated_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS session_summaries (
            id              SERIAL PRIMARY KEY,
            source_id       VARCHAR(100) NOT NULL,
            session_date    DATE,
            summary_text    TEXT NOT NULL,
            embedding       FLOAT8[],
            created_at      TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_session_summaries_date ON session_summaries (session_date);

        CREATE TABLE IF NOT EXISTS consolidated_facts (
            id                  SERIAL PRIMARY KEY,
            consolidated_text   TEXT NOT NULL,
            fact_ids            INTEGER[] NOT NULL DEFAULT '{}',
            metadata            JSONB DEFAULT '{}',
            consolidated_tsv    TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', consolidated_text)) STORED,
            source_id           VARCHAR(100),
            conversation_date   DATE,
            embedding           FLOAT8[],
            created_at          TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_consolidated_tsv ON consolidated_facts USING GIN (consolidated_tsv);
        CREATE INDEX IF NOT EXISTS idx_consolidated_date ON consolidated_facts (conversation_date);
    """)
    conn.commit()
    cur.close()

    # Migrate: add category_name column to existing atomic_facts table if missing
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'atomic_facts' AND column_name = 'category_name'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE atomic_facts ADD COLUMN category_name VARCHAR(100) DEFAULT 'general'")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON atomic_facts (category_name)")
        conn.commit()

    cur.close()
    release_connection(conn)

    # Seed default categories
    seed_default_categories()


# ── MemCell CRUD ──

def insert_memcell(cell: MemCell) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO memcells (episode_text, raw_dialogue, source_id, conversation_date) VALUES (%s, %s, %s, %s) RETURNING id",
        (cell.episode_text, cell.raw_dialogue, cell.source_id, cell.conversation_date)
    )
    cell_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return cell_id


# ── AtomicFact CRUD ──

def insert_atomic_fact(fact: AtomicFact) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO atomic_facts (memcell_id, fact_text, category_name, is_active, conversation_date) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (fact.memcell_id, fact.fact_text, fact.category_name, fact.is_active, fact.conversation_date)
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
                         date_filter: dict = None, exclude_ids: set = None) -> list[dict]:
    """Full-text search on atomic_facts using websearch_to_tsquery with OR logic.

    Args:
        date_filter: Optional {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}
        exclude_ids: Optional set of fact IDs to exclude from results
    """
    or_query = _to_or_query(query)
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    exclude_clause = ""
    exclude_params = []
    if exclude_ids:
        exclude_clause = " AND id != ALL(%s)"
        exclude_params = [list(exclude_ids)]

    if date_filter:
        cur.execute(f"""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM atomic_facts
            WHERE conversation_date BETWEEN %s AND %s
              AND is_active = TRUE
              AND fact_tsv @@ websearch_to_tsquery('english', %s)
              {exclude_clause}
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, date_filter["date_from"], date_filter["date_to"], or_query, *exclude_params, top_k))
    elif query_time:
        cur.execute(f"""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM atomic_facts
            WHERE conversation_date <= %s
              AND (superseded_on IS NULL OR superseded_on > %s)
              AND fact_tsv @@ websearch_to_tsquery('english', %s)
              {exclude_clause}
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, query_time, query_time, or_query, *exclude_params, top_k))
    else:
        cur.execute(f"""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, websearch_to_tsquery('english', %s), 32) AS rank
            FROM atomic_facts
            WHERE is_active = TRUE
              AND fact_tsv @@ websearch_to_tsquery('english', %s)
              {exclude_clause}
            ORDER BY rank DESC
            LIMIT %s
        """, (or_query, or_query, *exclude_params, top_k))
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
    """Return foresight valid at the given time, with embeddings and source conversation date.

    Args:
        query_time: IST datetime for all comparisons (conversation_date, valid_from, valid_until)
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT f.*, m.conversation_date AS source_date FROM foresight f
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



# ── Profile Categories ──────────────────────────────────────────────────────

DEFAULT_CATEGORIES = [
    ("personal_info", "Name, age, gender, nationality, origin, identity"),
    ("preferences", "Likes, dislikes, favorites (food, books, music, etc.)"),
    ("relationships", "Family, friends, partners, social connections"),
    ("activities", "Hobbies, sports, creative pursuits, regular activities"),
    ("goals", "Future plans, aspirations, deadlines, intentions"),
    ("experiences", "Past events, trips, milestones, achievements"),
    ("knowledge", "Skills, expertise, education, certifications"),
    ("opinions", "Views, beliefs, attitudes, reactions"),
    ("habits", "Routines, patterns, lifestyle choices, health"),
    ("work_life", "Career, job, professional development, workplace"),
]


def seed_default_categories():
    """Insert default categories if they don't already exist."""
    conn = get_connection()
    cur = conn.cursor()
    for name, desc in DEFAULT_CATEGORIES:
        cur.execute(
            """INSERT INTO profile_categories (category_name, description)
               VALUES (%s, %s) ON CONFLICT (category_name) DO NOTHING""",
            (name, desc)
        )
    conn.commit()
    cur.close()
    release_connection(conn)


def get_profile_category(category_name: str) -> dict | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM profile_categories WHERE category_name = %s", (category_name,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return dict(row) if row else None


def get_profile_categories(category_names: list[str]) -> list[dict]:
    """Fetch profile category summaries by name."""
    if not category_names:
        return []
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT category_name, summary_text, fact_count
           FROM profile_categories
           WHERE category_name = ANY(%s) AND summary_text != ''""",
        (category_names,)
    )
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(r) for r in rows]


def get_all_category_embeddings() -> list[dict]:
    """Fetch all category embeddings for in-memory cache (used by retrieval)."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT category_name, embedding FROM profile_categories WHERE embedding IS NOT NULL"
    )
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(r) for r in rows]


def upsert_profile_category(category_name: str, summary_text: str,
                             fact_count: int, embedding: list[float] = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO profile_categories (category_name, summary_text, fact_count, embedding)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (category_name) DO UPDATE SET
             summary_text = EXCLUDED.summary_text,
             fact_count = EXCLUDED.fact_count,
             embedding = EXCLUDED.embedding,
             updated_at = NOW()""",
        (category_name, summary_text, fact_count, embedding)
    )
    conn.commit()
    cur.close()
    release_connection(conn)


def get_active_facts_by_category(category_name: str) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT id, fact_text, conversation_date
           FROM atomic_facts
           WHERE category_name = %s AND is_active = TRUE
           ORDER BY conversation_date DESC""",
        (category_name,)
    )
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(r) for r in rows]


def get_categories_with_facts() -> list[str]:
    """Return category names that have at least one active fact."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT category_name FROM atomic_facts
           WHERE is_active = TRUE AND category_name IS NOT NULL"""
    )
    result = [row[0] for row in cur.fetchall()]
    cur.close()
    release_connection(conn)
    return result


def get_or_create_category(category_name: str, description: str = "") -> dict:
    """Get existing category or create a new one."""
    existing = get_profile_category(category_name)
    if existing:
        return existing
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """INSERT INTO profile_categories (category_name, description)
           VALUES (%s, %s)
           ON CONFLICT (category_name) DO NOTHING
           RETURNING *""",
        (category_name, description)
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    release_connection(conn)
    if row:
        return dict(row)
    return get_profile_category(category_name)


# ── Consolidated Facts CRUD ──

def insert_consolidated_fact(cf: ConsolidatedFact) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO consolidated_facts
            (consolidated_text, fact_ids, metadata, source_id, conversation_date, embedding)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (cf.consolidated_text, cf.fact_ids,
          json.dumps(cf.metadata) if cf.metadata else '{}',
          cf.source_id, cf.conversation_date, cf.embedding))
    row_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return row_id


def get_consolidated_facts_by_ids(cf_ids: list[int]) -> list[dict]:
    if not cf_ids:
        return []
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, consolidated_text, fact_ids, metadata, source_id, conversation_date
        FROM consolidated_facts WHERE id = ANY(%s)
    """, (cf_ids,))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(r) for r in rows]


def get_memcell_by_id(memcell_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM memcells WHERE id = %s", (memcell_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row


def get_memcells_by_ids(memcell_ids: list[int]) -> dict[int, dict]:
    """Batch fetch multiple memcells by ID in a single query. Returns {id: row_dict}."""
    if not memcell_ids:
        return {}
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM memcells WHERE id = ANY(%s)", (list(memcell_ids),))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return {row["id"]: row for row in rows}


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
        "total_conflicts": total_conflicts,
        "active_facts": active_facts,
        "total_facts": total_facts,
    }


# ── Conversation Summaries ──

def get_conversation_summary() -> str:
    """Get the current rolling conversation summary. Returns empty string if none exists."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT summary_text FROM conversation_summaries ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return row[0] if row else ""


def upsert_conversation_summary(summary_text: str):
    """Insert or update the rolling conversation summary."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM conversation_summaries LIMIT 1")
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE conversation_summaries SET summary_text = %s, updated_at = NOW() WHERE id = %s",
                    (summary_text, row[0]))
    else:
        cur.execute("INSERT INTO conversation_summaries (summary_text) VALUES (%s)", (summary_text,))
    conn.commit()
    cur.close()
    release_connection(conn)


# ── Session Summaries CRUD ──

def insert_session_summary(source_id: str, session_date: str, summary_text: str,
                           embedding: list[float] = None) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO session_summaries (source_id, session_date, summary_text, embedding)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (source_id, session_date, summary_text, embedding))
    row_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    release_connection(conn)
    return row_id


def get_session_summaries(limit: int = 5) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, source_id, session_date, summary_text
        FROM session_summaries ORDER BY session_date DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [{"id": r[0], "source_id": r[1], "session_date": r[2], "summary_text": r[3]}
            for r in rows]


def get_session_summaries_by_date_range(start_date: str, end_date: str) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, source_id, session_date, summary_text
        FROM session_summaries
        WHERE session_date >= %s AND session_date <= %s
        ORDER BY session_date
    """, (start_date, end_date))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [{"id": r[0], "source_id": r[1], "session_date": r[2], "summary_text": r[3]}
            for r in rows]
