import logging
from datetime import datetime
from typing import List

from app.ambiguity import finalize_confidence_and_ambiguities
from app.date_resolution import resolve_date_phrase
from app.extraction import EventExtractor, ExtractedEventDraft
from app.models import Event

logger = logging.getLogger("atlas.extraction_pipeline")


def extract_events_from_text(
    text: str, reference_datetime: datetime, timezone: str, extractor: EventExtractor
) -> List[Event]:
    """The one place text becomes Event objects -- used by both /extract and
    the Gmail candidates endpoint so there's a single implementation of the
    extract -> resolve -> finalize-ambiguity pipeline."""
    drafts = extractor.extract(text)
    logger.info(
        "extraction complete model=%s event_count=%d", extractor.model_name, len(drafts)
    )
    return [_build_event(draft, reference_datetime, timezone) for draft in drafts]


def _build_event(draft: ExtractedEventDraft, reference_datetime: datetime, timezone: str) -> Event:
    resolved = resolve_date_phrase(draft.date_phrase, reference_datetime, timezone)
    confidence, ambiguities = finalize_confidence_and_ambiguities(
        draft.confidence,
        draft.ambiguities,
        date_resolved=resolved.start is not None,
        date_phrase=draft.date_phrase,
    )

    return Event(
        title=draft.title,
        date_phrase=draft.date_phrase,
        resolved_start=resolved.start,
        resolved_end=resolved.end,
        all_day=resolved.all_day,
        location=draft.location,
        notes=draft.notes,
        source_excerpt=draft.source_excerpt,
        confidence=confidence,
        ambiguities=ambiguities,
        needs_confirmation=True,
    )
