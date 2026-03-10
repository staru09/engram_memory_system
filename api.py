import uuid
import json
import threading
import time
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL
import db
import vector_store
from main import ingest_conversation
from agentic_layer.fetch_mem_service import retrieve, compose_context


# ── Configuration ──

INGESTION_MESSAGE_THRESHOLD = 20
PERIODIC_CHECK_INTERVAL = 600  # seconds
PERIODIC_MIN_MESSAGES = 4      # minimum messages for time-based trigger

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ── Request/Response Models ──

class ChatRequest(BaseModel):
    thread_id: str
    message: str


class CreateThreadRequest(BaseModel):
    title: str | None = None


# ── Prompt Builder ──

def build_chat_prompt(memory_context: str, recent_messages: list[dict],
                      query_time: datetime) -> str:
    history_lines = []
    for msg in recent_messages:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_lines.append(f"{role}: {msg['content']}")
    history_block = "\n".join(history_lines)

    return f"""You are "Ira", a close friend and AI companion who chats casually in Hinglish (Hindi words written in English script). You have long-term memory of past conversations.

LANGUAGE RULES:
- Always reply in Hinglish — Hindi sentences written in Roman/English script (e.g. "arre waah!", "haan yaar bilkul", "kya baat hai!")
- Use casual, friendly tone like texting a close friend — short messages, slang, emojis occasionally
- Use "yaar", "bhai", "arre", "accha", "haan", "nahi" naturally
- Keep responses SHORT — 1-3 short sentences max, like a real WhatsApp chat. Do NOT write paragraphs.
- Match the user's energy — if they're excited, be excited. If they're chill, be chill.
- Ask follow-up questions to keep the conversation going, like a real friend would
- You can use English words mixed in naturally (like real Hinglish speakers do)
- Never be formal. Never use "aap". Always use "tu/tum".

MEMORY RULES:
- You have two sources of context: MEMORY CONTEXT (long-term) and RECENT CHAT (short-term)
- Use both to give informed responses. When they conflict, trust the most recent info.
- Do NOT cite dates or say "according to my memory". Just naturally bring up things you remember, like a real friend would (e.g. "arre tune kal bataya tha na ki...")
- If you don't remember something, say so naturally (e.g. "hmm ye toh yaad nahi yaar")
- NEVER invent, assume, or guess facts that are not present in MEMORY CONTEXT or RECENT CHAT. If you don't know something, admit it naturally (e.g. "ye toh mujhe nahi pata yaar", "tune bataya nahi tha ye")
- Do NOT extrapolate outcomes of events unless the user explicitly told you what happened

=== CURRENT TIME ===
{query_time.strftime('%Y-%m-%d %H:%M')}

=== MEMORY CONTEXT (from past conversations) ===
{memory_context if memory_context else "Koi purani memory nahi mili."}

=== RECENT CHAT ===
{history_block}

Respond to the user's latest message naturally in Hinglish. Keep it short like a WhatsApp text."""


# ── Background Ingestion ──

_ingestion_lock = threading.Lock()
_ingesting_threads: set[str] = set()


def run_background_ingestion(thread_id: str, messages: list[dict]):
    """Convert chat messages to conversation format and run existing pipeline."""
    try:
        conversation = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
        ]
        current_date = messages[-1]["created_at"].strftime("%Y-%m-%d")
        source_id = f"thread_{thread_id}_{current_date}"

        ingest_conversation(conversation, source_id=source_id, current_date=current_date)

        message_ids = [msg["id"] for msg in messages]
        db.mark_messages_ingested(message_ids)
        print(f"[Background] Ingested {len(messages)} messages from thread {thread_id}")
    except Exception as e:
        print(f"[Background] Ingestion failed for thread {thread_id}: {e}")
    finally:
        with _ingestion_lock:
            _ingesting_threads.discard(thread_id)


def check_ingestion_trigger(thread_id: str):
    """Trigger background ingestion if unprocessed message count exceeds threshold."""
    with _ingestion_lock:
        if thread_id in _ingesting_threads:
            return  # already ingesting
    unprocessed = db.get_unprocessed_messages(thread_id)
    if len(unprocessed) >= INGESTION_MESSAGE_THRESHOLD:
        with _ingestion_lock:
            if thread_id in _ingesting_threads:
                return
            _ingesting_threads.add(thread_id)
        threading.Thread(
            target=run_background_ingestion,
            args=(thread_id, unprocessed),
            daemon=True
        ).start()


def periodic_ingestion_check():
    """Periodically check for threads with old unprocessed messages."""
    while True:
        time.sleep(PERIODIC_CHECK_INTERVAL)
        try:
            thread_ids = db.get_threads_with_old_unprocessed(minutes=10)
            for thread_id in thread_ids:
                with _ingestion_lock:
                    if thread_id in _ingesting_threads:
                        continue
                unprocessed = db.get_unprocessed_messages(thread_id)
                if len(unprocessed) >= PERIODIC_MIN_MESSAGES:
                    with _ingestion_lock:
                        _ingesting_threads.add(thread_id)
                    threading.Thread(
                        target=run_background_ingestion,
                        args=(thread_id, unprocessed),
                        daemon=True
                    ).start()
        except Exception as e:
            print(f"[Periodic] Check failed: {e}")


# ── App Lifecycle ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    vector_store.init_collections()
    # Start periodic ingestion checker
    threading.Thread(target=periodic_ingestion_check, daemon=True).start()
    yield


# ── FastAPI App ──

app = FastAPI(title="Engram Memory Chat", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ──

@app.post("/threads")
def create_thread_endpoint(request: CreateThreadRequest = None):
    thread_id = str(uuid.uuid4())
    title = request.title if request else None
    db.create_thread(thread_id, title)
    return {"thread_id": thread_id}


@app.get("/threads")
def list_threads_endpoint(limit: int = 20):
    threads = db.list_threads(limit)
    return {"threads": threads}


@app.get("/threads/{thread_id}/messages")
def get_messages(thread_id: str, limit: int = 50, before_id: int = None):
    messages = db.get_thread_messages(thread_id, limit, before_id)
    has_more = len(messages) == limit
    return {"messages": messages, "has_more": has_more}


@app.post("/chat")
def chat(request: ChatRequest):
    # 0. Ensure thread exists (handles stale localStorage after DB reset)
    if not db.get_thread(request.thread_id):
        db.create_thread(request.thread_id)

    # 1. Store user message
    db.insert_message(request.thread_id, "user", request.message)

    # 2. Get unindexed messages as short-term memory (everything not yet in long-term memory)
    query_time = datetime.now()
    recent_messages = db.get_unprocessed_messages(request.thread_id)

    # 3. Retrieve memory context (long-term memory) — skip if no data yet
    memory_context = ""
    try:
        stats = db.get_system_stats()
        if stats["active_facts"] > 0:
            result = retrieve(request.message, query_time)
            memory_context = compose_context(result)
    except Exception as e:
        print(f"[Chat] Memory retrieval failed: {e}")

    # 4. Build prompt
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time,
    )

    # 5. Call Gemini
    response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    answer = response.text.strip()

    # 6. Store assistant response
    db.insert_message(request.thread_id, "assistant", answer)

    # 7. Check if background ingestion should trigger
    check_ingestion_trigger(request.thread_id)

    return {"response": answer, "thread_id": request.thread_id}


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    # 0. Ensure thread exists (handles stale localStorage after DB reset)
    if not db.get_thread(request.thread_id):
        db.create_thread(request.thread_id)

    # 1. Store user message
    db.insert_message(request.thread_id, "user", request.message)

    # 2. Get unindexed messages as short-term memory (everything not yet in long-term memory)
    query_time = datetime.now()
    recent_messages = db.get_unprocessed_messages(request.thread_id)

    memory_context = ""
    try:
        stats = db.get_system_stats()
        if stats["active_facts"] > 0:
            result = retrieve(request.message, query_time)
            memory_context = compose_context(result)
    except Exception as e:
        print(f"[Chat Stream] Memory retrieval failed: {e}")

    # 3. Build prompt
    prompt = build_chat_prompt(
        memory_context=memory_context,
        recent_messages=recent_messages,
        query_time=query_time,
    )

    # 5. Stream from Gemini
    async def generate():
        response = gemini_client.models.generate_content_stream(
            model=GEMINI_MODEL, contents=prompt
        )
        full_response = []
        for chunk in response:
            text = chunk.text
            full_response.append(text)
            yield f"data: {json.dumps({'text': text})}\n\n"

        # Store full response after stream completes
        answer = "".join(full_response)
        db.insert_message(request.thread_id, "assistant", answer)
        check_ingestion_trigger(request.thread_id)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/threads/{thread_id}/ingest")
def trigger_ingestion(thread_id: str):
    """Manually trigger ingestion for a thread."""
    unprocessed = db.get_unprocessed_messages(thread_id)
    if not unprocessed:
        return {"status": "no_unprocessed_messages", "count": 0}

    with _ingestion_lock:
        if thread_id in _ingesting_threads:
            return {"status": "already_ingesting"}
        _ingesting_threads.add(thread_id)

    threading.Thread(
        target=run_background_ingestion,
        args=(thread_id, unprocessed),
        daemon=True
    ).start()

    return {"status": "ingestion_started", "message_count": len(unprocessed)}


@app.get("/stats")
def get_stats():
    return db.get_system_stats()
