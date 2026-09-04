import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.chat import ChatEngine, ChatMessage, build_system_prompt, get_chat_engine
from app.config import PERSONA
from app.extraction import EventExtractor, get_extractor
from app.memory import MemoryStore, get_memory_store
from app.rate_limit import enforce_chat_rate_limit
from app.supabase_client import AuthenticatedUser, get_current_user
from app.tools import build_tools
from app.weather import WeatherClient, get_weather_client
from app.web_search import WebSearchClient, get_web_search_client

logger = logging.getLogger("atlas.chat")

router = APIRouter()


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    reference_datetime: datetime
    timezone: str


class ChatResponse(BaseModel):
    new_messages: list[ChatMessage]


@router.post("/chat")
def chat(
    request: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    _rate_limit: None = Depends(enforce_chat_rate_limit),
    engine: ChatEngine = Depends(get_chat_engine),
    extractor: EventExtractor = Depends(get_extractor),
    weather_client: WeatherClient = Depends(get_weather_client),
    search_client: WebSearchClient = Depends(get_web_search_client),
    memory_store: MemoryStore = Depends(get_memory_store),
) -> ChatResponse:
    if not request.messages or request.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="last message must be from the user")

    messages = request.messages
    if messages[0].role != "system":
        query_text = request.messages[-1].content
        facts: List[str] = []
        if query_text:
            try:
                facts = memory_store.search_facts(user.id, query_text, limit=10)
            except Exception:
                # A transient memory-read failure (now also covering an
                # embedding-API failure) should degrade the assistant to "no
                # recalled facts this turn," not fail the entire chat request
                # -- unlike remember_fact's write path (where a real DB error
                # should surface, since nothing was silently lost either
                # way), a read failure here has a safe, harmless fallback
                # that's clearly better than a 500 for a whole chat turn.
                logger.warning("failed to load remembered facts for chat context", exc_info=True)
                facts = []
        messages = [ChatMessage(role="system", content=build_system_prompt(PERSONA, facts))] + messages

    tools = build_tools(
        request.reference_datetime,
        request.timezone,
        extractor,
        weather_client,
        search_client,
        user.id,
        memory_store,
    )

    try:
        new_messages = engine.run_turn(messages, tools=tools)
    except Exception:
        logger.exception("chat turn failed model=%s", engine.model_name)
        raise HTTPException(status_code=502, detail="chat failed")

    return ChatResponse(new_messages=new_messages)
