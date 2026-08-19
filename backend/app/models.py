from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    text: str
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
