from datetime import datetime
from typing import Any, Dict, List

from app.chat import ToolDefinition
from app.extraction import EventExtractor
from app.extraction_pipeline import extract_events_from_text


def _build_email_to_calendar_tool(
    reference_datetime: datetime, timezone: str, extractor: EventExtractor
) -> ToolDefinition:
    def handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        events = extract_events_from_text(arguments["text"], reference_datetime, timezone, extractor)
        return {"events": [event.model_dump(mode="json") for event in events]}

    return ToolDefinition(
        name="extract_calendar_events",
        description=(
            "Extract candidate calendar events (title, date/time, location, etc.) "
            "from a block of text such as an email or message the user pasted or "
            "described. Returns proposed events for the user to review and "
            "describe back -- nothing is ever added to the calendar from here."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The email or message text to extract events from.",
                }
            },
            "required": ["text"],
        },
        handler=handler,
    )


def build_tools(reference_datetime: datetime, timezone: str, extractor: EventExtractor) -> List[ToolDefinition]:
    """The chat endpoint's per-request tool list.

    Tools are built fresh per request, not held as a static module-level
    registry, because email-to-calendar (and future tools like reminders)
    need the request's own reference_datetime/timezone -- date math must
    stay in code, never left for the model to compute (see CLAUDE.md
    invariants) -- plus the extractor dependency.
    """
    return [_build_email_to_calendar_tool(reference_datetime, timezone, extractor)]
