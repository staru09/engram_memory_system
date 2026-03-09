import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DB
from models import MemCell, AtomicFact, Foresight, MemScene, Conflict, UserProfile


def get_connection():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DB
    )


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
    """)
    conn.commit()
    cur.close()
    conn.close()


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
    conn.close()
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
    conn.close()


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
    conn.close()
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
    conn.close()


def get_memcells_by_scene(scene_id: int, query_time=None) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if query_time:
        cur.execute(
            "SELECT * FROM memcells WHERE scene_id = %s AND conversation_date <= %s ORDER BY created_at",
            (scene_id, query_time)
        )
    else:
        cur.execute("SELECT * FROM memcells WHERE scene_id = %s ORDER BY created_at", (scene_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


# ── AtomicFact CRUD ──

def insert_atomic_fact(fact: AtomicFact) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO atomic_facts (memcell_id, fact_text, is_active, conversation_date) VALUES (%s, %s, %s, %s) RETURNING id",
        (fact.memcell_id, fact.fact_text, fact.is_active, fact.conversation_date)
    )
    fact_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
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
    conn.close()


def keyword_search_facts(query: str, top_k: int = 10, query_time=None) -> list[dict]:
    """Full-text search on atomic_facts using ts_rank."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if query_time:
        cur.execute("""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, plainto_tsquery('english', %s)) AS rank
            FROM atomic_facts
            WHERE conversation_date <= %s
              AND (superseded_on IS NULL OR superseded_on > %s)
              AND fact_tsv @@ plainto_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """, (query, query_time, query_time, query, top_k))
    else:
        cur.execute("""
            SELECT id, memcell_id, fact_text, conversation_date,
                   ts_rank(fact_tsv, plainto_tsquery('english', %s)) AS rank
            FROM atomic_facts
            WHERE is_active = TRUE
              AND fact_tsv @@ plainto_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """, (query, query, top_k))
    rows = cur.fetchall()
    cur.close()
    conn.close()
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
    conn.close()
    return fid


def get_active_foresight(query_time) -> list[dict]:
    """Return foresight valid at the given time, with embeddings and source conversation date."""
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
    conn.close()
    return rows


def update_foresight_embedding(foresight_id: int, embedding: list[float]):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE foresight SET embedding = %s WHERE id = %s", (embedding, foresight_id))
    conn.commit()
    cur.close()
    conn.close()


def update_memcell_embedding(memcell_id: int, embedding: list[float]):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE memcells SET embedding = %s WHERE id = %s", (embedding, memcell_id))
    conn.commit()
    cur.close()
    conn.close()


# ── Conflict CRUD ──

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
    conn.close()
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
    conn.close()
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
    conn.close()


def get_user_profile() -> UserProfile | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM user_profile LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return UserProfile(
        id=row["id"],
        explicit_facts=row["explicit_facts"],
        implicit_traits=row["implicit_traits"],
        updated_at=row["updated_at"]
    )


def get_memcell_by_id(memcell_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM memcells WHERE id = %s", (memcell_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_fact_by_id(fact_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM atomic_facts WHERE id = %s", (fact_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


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
    conn.close()
    return valid


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
    conn.close()
    return {
        "total_memcells": total_memcells,
        "total_scenes": total_scenes,
        "total_conflicts": total_conflicts,
        "active_facts": active_facts,
        "total_facts": total_facts,
    }
