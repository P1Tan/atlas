import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.chat import (
    ChatEngine,
    ChatMessage,
    build_memory_note,
    build_system_prompt,
    get_chat_engine,
)
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

# Found live (bug audit): unlike /extract (fixed in the same audit) and
# every other text-ingestion path in the app, /chat had no size bound on
# message content or count at all -- a single oversized message (or a huge
# array of them) reached the LLM directly, and could ALSO reach the costly
# extract_calendar_events tool call one hop further in (see tools.py's own
# matching fix). The two bounds are enforced differently on purpose:
#
#   - _MAX_CHAT_MESSAGE_CHARS is a 422. A single oversized message is a raw
#     client submission the caller can fix, so telling them to resubmit
#     within bounds is the right shape -- same reasoning /extract's own cap
#     already uses -- and silently truncating someone's text would change
#     what they asked, not just how much history came along with it.
#   - _MAX_CHAT_MESSAGES trims instead (review finding F7). This one was a
#     422 too, which was wrong in practice: iOS resends its ENTIRE in-memory
#     conversation on every /chat call, so the 501st turn would 422 and
#     every subsequent turn would too -- the conversation was permanently
#     dead until the app was relaunched, with no way for the user to
#     understand or recover from it. Dropping the oldest turns is both
#     recoverable and what the voice path already does for the same reason
#     (voice_transcript_bridge.py's `sanitized[-_MAX_SEED_MESSAGES:]`).
_MAX_CHAT_MESSAGE_CHARS = 20_000
_MAX_CHAT_MESSAGES = 500


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    reference_datetime: datetime
    timezone: str
    # From the device's own location services (reverse-geocoded on iOS), a
    # short "City, Region" string or omitted entirely if permission was
    # never granted -- not required, purely additive context.
    user_location: Optional[str] = None


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
    # Found live: a last message with neither content nor tool_calls passed
    # this far and reached the OpenAI call, which rejects a user-role
    # message with nothing in it -- surfacing as an opaque 502 "chat
    # failed" (the outer try/except's blanket catch) instead of the 422
    # this actually is. (It still costs the caller a rate-limit slot:
    # enforce_chat_rate_limit is a dependency, so it has already recorded a
    # hit by the time this body runs. Moving the structural checks into a
    # dependency declared ahead of the limiter would change that, but it
    # would also mean an unauthenticated/abusive caller could probe /chat
    # for free -- not obviously the better trade, so the cost stays.)
    # Checked against tool_calls too, not just content: a message with tool_calls
    # but no content is legitimate and already relied on elsewhere
    # (test_chat_skips_search_facts_when_last_message_has_no_content) --
    # only truly empty (neither) is the actual bug.
    last_message = request.messages[-1]
    if not (last_message.content or "").strip() and not last_message.tool_calls:
        raise HTTPException(status_code=422, detail="the last message must have content or tool_calls")
    for message in request.messages:
        if message.content and len(message.content) > _MAX_CHAT_MESSAGE_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"a message exceeds the {_MAX_CHAT_MESSAGE_CHARS}-character limit",
            )

    messages = request.messages
    if len(messages) > _MAX_CHAT_MESSAGES:
        # Keep the most recent turns and drop the oldest (see the
        # _MAX_CHAT_MESSAGES comment above for why this trims rather than
        # 422s). A leading system message is carried over rather than
        # dropped with the rest of the head: it's the client's own prompt
        # for this conversation, not history, and losing it would silently
        # change the assistant's behaviour mid-conversation -- and the
        # "no system message? build one" branch just below would then
        # rebuild a DIFFERENT prompt from the default persona.
        system_prefix = messages[:1] if messages[0].role == "system" else []
        recent_count = _MAX_CHAT_MESSAGES - len(system_prefix)
        messages = system_prefix + messages[-recent_count:]
        logger.info(
            "trimmed an over-long chat history to the most recent %s messages",
            _MAX_CHAT_MESSAGES,
        )
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
        prompt = build_system_prompt(PERSONA, facts, user_location=request.user_location)
        messages = [ChatMessage(role="system", content=prompt)] + messages
        if facts:
            # The same facts, restated as a system message sitting directly
            # in front of the user's current question. Found live: with the
            # facts only in the top system prompt, a replayed history
            # containing remember_fact("My brother lives in San
            # Francisco.") + its tool result + the assistant's confirmation,
            # with the stored fact then edited to "San Diego", answered
            # "where does my brother live?" correctly 1/3 times against
            # gpt-5-mini; adding this note before the final user message
            # made it 3/3. M1's precedence paragraph is right but buried
            # behind the whole transcript by the time the model answers.
            #
            # Per-request and ephemeral: OpenAIChatEngine.run_turn returns
            # only the messages it generated this turn (assistant/tool), so
            # the note never reaches the client, and the client only ever
            # echoes back messages it was given -- there's no path for it
            # to accumulate in the history.
            messages = messages[:-1] + [
                ChatMessage(role="system", content=build_memory_note(facts)),
                messages[-1],
            ]

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
