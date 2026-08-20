import logging

from fastapi import Depends, FastAPI, HTTPException

from app.extraction import EventExtractor, get_default_extractor
from app.models import Event, ExtractRequest

logger = logging.getLogger("atlas.api")

app = FastAPI(title="Atlas backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def get_extractor() -> EventExtractor:
    return get_default_extractor()


@app.post("/extract")
def extract(
    request: ExtractRequest, extractor: EventExtractor = Depends(get_extractor)
) -> list[Event]:
    try:
        drafts = extractor.extract(request.text)
    except Exception:
        logger.exception("extraction failed model=%s", extractor.model_name)
        raise HTTPException(status_code=502, detail="extraction failed")

    logger.info(
        "extraction complete model=%s event_count=%d", extractor.model_name, len(drafts)
    )

    return [
        Event(
            title=draft.title,
            date_phrase=draft.date_phrase,
            all_day=draft.all_day,
            location=draft.location,
            notes=draft.notes,
            source_excerpt=draft.source_excerpt,
            confidence=draft.confidence,
            ambiguities=draft.ambiguities,
            needs_confirmation=True,
        )
        for draft in drafts
    ]
