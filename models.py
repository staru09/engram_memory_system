from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AtomicFact:
    id: Optional[int] = None
    memcell_id: Optional[int] = None
    fact_text: str = ""
    category_name: str = "general"
    is_active: bool = True
    created_at: Optional[datetime] = None
    conversation_date: Optional[str] = None
    superseded_on: Optional[str] = None


@dataclass
class ProfileCategory:
    id: Optional[int] = None
    category_name: str = ""
    summary_text: str = ""
    fact_count: int = 0
    embedding: Optional[list[float]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class ConsolidatedFact:
    id: Optional[int] = None
    consolidated_text: str = ""
    fact_ids: list[int] = field(default_factory=list)
    metadata: Optional[dict] = field(default_factory=dict)
    source_id: str = ""
    conversation_date: Optional[str] = None
    embedding: Optional[list[float]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Foresight:
    id: Optional[int] = None
    memcell_id: Optional[int] = None
    description: str = ""
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None  # None = indefinite
    created_at: Optional[datetime] = None


@dataclass
class MemCell:
    id: Optional[int] = None
    episode_text: str = ""
    raw_dialogue: str = ""
    created_at: Optional[datetime] = None
    source_id: str = ""
    conversation_date: Optional[str] = None
    facts: list[AtomicFact] = field(default_factory=list)
    foresight: list[Foresight] = field(default_factory=list)


@dataclass
class Conflict:
    id: Optional[int] = None
    old_fact_id: Optional[int] = None
    new_fact_id: Optional[int] = None
    resolution: str = "recency_wins"
    detected_at: Optional[datetime] = None


@dataclass
class UserProfile:
    id: Optional[int] = None
    explicit_facts: list[str] = field(default_factory=list)
    implicit_traits: list[str] = field(default_factory=list)
    updated_at: Optional[datetime] = None


@dataclass
class ChatThread:
    id: str = ""
    title: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class ChatMessage:
    id: Optional[int] = None
    thread_id: str = ""
    role: str = ""  # 'user' or 'assistant'
    content: str = ""
    created_at: Optional[datetime] = None
    ingested: bool = False


@dataclass
class QueryLog:
    id: Optional[int] = None
    thread_id: Optional[str] = None
    query_text: str = ""
    response_text: str = ""
    memory_context: str = ""
    retrieval_metadata: Optional[dict] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    query_time: Optional[datetime] = None
