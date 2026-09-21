from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Matches gmail_client.MAX_BODY_CHARS -- /extract is the one text-ingestion
# path in the app that had no size bound at all (Gmail bodies and voice's
# incoming transcript text are both capped elsewhere). Found live during a
# bug audit: combined with /extract having no auth or rate limiting either
# (see rate_limit.py's own comment on that), an unbounded body was an open
# vector for arbitrarily expensive/costly OpenAI calls from any caller.
_MAX_EXTRACT_TEXT_CHARS = 20_000


class ExtractRequest(BaseModel):
    text: str = Field(max_length=_MAX_EXTRACT_TEXT_CHARS)
    reference_datetime: datetime
    timezone: str


class Event(BaseModel):
    title: str
    date_phrase: str
    resolved_start: Optional[datetime] = None
    resolved_end: Optional[datetime] = None
    all_day: bool = False
    location: Optional[str] = None
    notes: Optional[str] = None
    source_excerpt: str
    confidence: Literal["high", "medium", "low"]
    ambiguities: list[str] = Field(default_factory=list)
    needs_confirmation: bool = True
