from pydantic import BaseModel


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class CreateThreadRequest(BaseModel):
    title: str | None = None
