import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel

from app.extraction import EventExtractor, get_extractor
from app.extraction_pipeline import extract_events_from_text
from app.gmail_client import fetch_recent_unread_messages
from app.google_auth import load_credentials, save_credentials
from app.models import Event

logger = logging.getLogger("atlas.gmail")

router = APIRouter(prefix="/gmail", tags=["gmail"])

# Hard ceiling regardless of what a caller requests -- never sweep the whole
# inbox, per the email-privacy invariant.
MAX_RESULTS_CAP = 20


class GmailCandidate(BaseModel):
    message_id: str
    subject: str
    events: List[Event]


@router.get("/candidates", response_model=List[GmailCandidate])
def get_candidates(
    reference_datetime: datetime,
    timezone: str,
    max_results: int = Query(10, ge=1, le=MAX_RESULTS_CAP),
    extractor: EventExtractor = Depends(get_extractor),
) -> List[GmailCandidate]:
    credentials = load_credentials()
    if credentials is None:
        raise HTTPException(status_code=401, detail="Gmail not connected")

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleAuthRequest())
        save_credentials(credentials)

    try:
        messages = fetch_recent_unread_messages(credentials, max_results=max_results)
    except Exception:
        logger.exception("Gmail fetch failed")
        raise HTTPException(status_code=502, detail="failed to fetch Gmail messages")

    logger.info("gmail fetch complete message_count=%d", len(messages))

    candidates: List[GmailCandidate] = []
    for message in messages:
        try:
            events = extract_events_from_text(
                message.body_text, reference_datetime, timezone, extractor
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception:
            logger.exception("extraction failed for a Gmail message, skipping it")
            continue

        candidates.append(
            GmailCandidate(message_id=message.id, subject=message.subject, events=events)
        )

    return candidates
