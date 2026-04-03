from pydantic import BaseModel


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class CreateThreadRequest(BaseModel):
    title: str | None = None


class QueryRequest(BaseModel):
    query: str
    thread_id: str | None = None
