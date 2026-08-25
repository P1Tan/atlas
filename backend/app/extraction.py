import json
import logging
from typing import List, Literal, Optional, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import EXTRACTION_MODEL

logger = logging.getLogger("atlas.extraction")

SYSTEM_PROMPT = (
    "You extract calendar events from arbitrary text (often emails). "
    "For each event, capture the temporal expression exactly as written in "
    "date_phrase -- never resolve or guess an absolute date yourself, that is "
    "done by other code. If a year, exact time, or other detail is genuinely "
    "ambiguous or missing, note it in ambiguities and lower confidence rather "
    "than guessing. If the text contains no schedulable event, return an "
    "empty events list."
)

_EXTRACT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "record_events",
        "description": "Record calendar events found in the given text.",
        "parameters": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Concise event title",
                            },
                            "date_phrase": {
                                "type": "string",
                                "description": (
                                    "The temporal expression exactly as found in the "
                                    "text, e.g. 'next Thursday 3pm'. Do not resolve it "
                                    "to a date yourself."
                                ),
                            },
                            "all_day": {"type": "boolean"},
                            "location": {"type": ["string", "null"]},
                            "notes": {"type": ["string", "null"]},
                            "source_excerpt": {
                                "type": "string",
                                "description": (
                                    "The sentence(s) in the source text the event "
                                    "was derived from."
                                ),
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                            "ambiguities": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "e.g. 'year not specified', 'two possible "
                                    "times'. Empty if none."
                                ),
                            },
                        },
                        "required": [
                            "title",
                            "date_phrase",
                            "all_day",
                            "source_excerpt",
                            "confidence",
                            "ambiguities",
                        ],
                    },
                }
            },
            "required": ["events"],
        },
    },
}


class ExtractedEventDraft(BaseModel):
    title: str
    date_phrase: str
    all_day: bool = False
    location: Optional[str] = None
    notes: Optional[str] = None
    source_excerpt: str
    confidence: Literal["high", "medium", "low"]
    ambiguities: List[str] = Field(default_factory=list)


class EventExtractor(Protocol):
    model_name: str

    def extract(self, text: str) -> List[ExtractedEventDraft]: ...


class OpenAIEventExtractor:
    """Extracts candidate events from text via OpenAI tool calling."""

    def __init__(self, model_name: str = EXTRACTION_MODEL, client: Optional[OpenAI] = None) -> None:
        self.model_name = model_name
        self._client = client or OpenAI()

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        response = self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=[_EXTRACT_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": "record_events"}},
        )

        message = response.choices[0].message
        tool_calls = message.tool_calls or []
        if not tool_calls:
            return []

        arguments = json.loads(tool_calls[0].function.arguments)
        raw_events = arguments.get("events", [])
        return [ExtractedEventDraft.model_validate(raw) for raw in raw_events]


def get_default_extractor() -> EventExtractor:
    return OpenAIEventExtractor()


def get_extractor() -> EventExtractor:
    """FastAPI dependency -- the one function every route depending on an
    extractor should use, so dependency_overrides in tests actually take
    effect everywhere rather than silently missing routes that redefined
    their own copy of this function."""
    return get_default_extractor()
