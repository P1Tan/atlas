import logging

from fastapi import Depends, FastAPI, HTTPException

from app.auth_routes import router as auth_router
from app.chat_routes import router as chat_router
from app.extraction import EventExtractor, get_extractor
from app.extraction_pipeline import extract_events_from_text
from app.gmail_routes import router as gmail_router
from app.memory_routes import router as memory_router
from app.models import Event, ExtractRequest

# Without this, every logger.info() in the app (extraction/model logging,
# Gmail fetch counts) is silently dropped -- the root logger defaults to
# WARNING and nothing else in this codebase raised it. Only logger.exception()
# calls were ever visible before this.
logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("atlas.api")

app = FastAPI(title="Atlas backend")
app.include_router(auth_router)
app.include_router(gmail_router)
app.include_router(chat_router)
app.include_router(memory_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/extract")
def extract(
    request: ExtractRequest, extractor: EventExtractor = Depends(get_extractor)
) -> list[Event]:
    try:
        return extract_events_from_text(
            request.text, request.reference_datetime, request.timezone, extractor
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.exception("extraction failed model=%s", extractor.model_name)
        raise HTTPException(status_code=502, detail="extraction failed")
