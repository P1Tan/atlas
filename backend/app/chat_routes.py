import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.chat import ChatEngine, ChatMessage, SYSTEM_PROMPT, get_chat_engine
from app.tools import REGISTERED_TOOLS

logger = logging.getLogger("atlas.chat")

router = APIRouter()


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    reference_datetime: datetime
    timezone: str


class ChatResponse(BaseModel):
    new_messages: list[ChatMessage]


@router.post("/chat")
def chat(request: ChatRequest, engine: ChatEngine = Depends(get_chat_engine)) -> ChatResponse:
    if not request.messages or request.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="last message must be from the user")

    messages = request.messages
    if messages[0].role != "system":
        messages = [ChatMessage(role="system", content=SYSTEM_PROMPT)] + messages

    try:
        new_messages = engine.run_turn(messages, tools=REGISTERED_TOOLS)
    except Exception:
        logger.exception("chat turn failed model=%s", engine.model_name)
        raise HTTPException(status_code=502, detail="chat failed")

    return ChatResponse(new_messages=new_messages)
