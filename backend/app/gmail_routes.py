import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel

from app.extraction import EventExtractor, get_extractor
from app.extraction_pipeline import extract_events_from_text
from app.gmail_client import fetch_recent_unread_messages
from app.google_auth import clear_credentials, load_credentials, save_credentials
from app.models import Event
from app.rate_limit import enforce_gmail_candidates_rate_limit
from app.supabase_client import AuthenticatedUser, get_current_user

logger = logging.getLogger("atlas.gmail")

router = APIRouter(prefix="/gmail", tags=["gmail"])

# Hard ceiling regardless of what a caller requests -- never sweep the whole
# inbox, per the email-privacy invariant.
MAX_RESULTS_CAP = 20

# The client sends the ids it has already reviewed; bound that list so a
# buggy (or hostile) caller can't turn a query string into an unbounded
# set-membership payload. Gmail ids are ~16 hex chars, so 64 is generous.
MAX_EXCLUDE_IDS = 200
MAX_EXCLUDE_ID_CHARS = 64

# This route can now return 401 for two genuinely different reasons, and a
# client has to tell them apart: the request wasn't authenticated at all
# (`get_current_user` -> "missing bearer token" / "invalid or expired
# token"), versus the request was fine but no Google account is linked to
# the backend. Only the second one means "tap Connect Gmail"; showing that
# for the first would send a signed-out user to reconnect an account that
# was never the problem. The auth dependency's own 401s are deliberately
# left as-is (shared with every other route), so the distinguishing signal
# is this exact `detail` string -- kept as a constant so it can't drift
# away from what clients and tests branch on.
GMAIL_NOT_CONNECTED_DETAIL = "Gmail not connected"


class GmailCandidate(BaseModel):
    message_id: str
    subject: str
    events: List[Event]


class GmailCandidatesResponse(BaseModel):
    candidates: List[GmailCandidate]
    # How many unread messages were skipped because the client had already
    # reviewed them -- without it a check that legitimately found nothing new
    # is indistinguishable from a broken one, and the user can't tell why
    # mail they can still see unread in Gmail stopped being offered.
    skipped_reviewed_count: int


@router.get("/candidates", response_model=GmailCandidatesResponse)
def get_candidates(
    reference_datetime: datetime,
    timezone: str,
    max_results: int = Query(10, ge=1, le=MAX_RESULTS_CAP),
    exclude_message_ids: List[str] = Query(default=[]),
    user: AuthenticatedUser = Depends(get_current_user),
    _rate_limit: None = Depends(enforce_gmail_candidates_rate_limit),
    extractor: EventExtractor = Depends(get_extractor),
) -> GmailCandidatesResponse:
    """Reads the signed-in user's unread mail and extracts event candidates.

    `user` is unused in the body on purpose: this is still the single-user
    Gmail connection stored server-side by `google_auth`, so there is no
    per-user mailbox to select. The dependency is here because this route
    reads a real inbox and spends real LLM calls doing it, and until now
    anyone who could reach the port could trigger both without presenting a
    token. Two distinct 401s are possible -- see
    `GMAIL_NOT_CONNECTED_DETAIL`.
    """
    if len(exclude_message_ids) > MAX_EXCLUDE_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"exclude_message_ids accepts at most {MAX_EXCLUDE_IDS} entries",
        )
    if any(not mid or len(mid) > MAX_EXCLUDE_ID_CHARS for mid in exclude_message_ids):
        raise HTTPException(
            status_code=422,
            detail=(
                "each exclude_message_ids entry must be non-empty and at most "
                f"{MAX_EXCLUDE_ID_CHARS} characters"
            ),
        )

    credentials = load_credentials()
    if credentials is None:
        raise HTTPException(status_code=401, detail=GMAIL_NOT_CONNECTED_DETAIL)

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(GoogleAuthRequest())
        except RefreshError:
            # Found live: an uncaught RefreshError here surfaced as a bare
            # 500 with no detail -- iOS only special-cases 401 as "not
            # connected" (ExtractionViewModel.checkGmail), so this left the
            # user staring at a generic "server returned 500" with no way to
            # know reconnecting would fix it. Most likely trigger for an
            # unverified/"Testing"-status Google OAuth app (this one, per
            # auth_routes.py's own comment): Google expires refresh tokens
            # after 7 days regardless of use in that mode, so a credential
            # that's just sat unused for a while is expected to eventually
            # fail exactly like this, not a rare edge case. The stored
            # credential is unusable either way once this happens -- clear
            # it so `/auth/google/status` (and the app's "Connect Gmail" vs.
            # "Check Gmail" button choice) reflects reality instead of
            # claiming a connection that no longer works.
            clear_credentials()
            raise HTTPException(status_code=401, detail=GMAIL_NOT_CONNECTED_DETAIL)
        save_credentials(credentials)

    try:
        messages, skipped_reviewed_count = fetch_recent_unread_messages(
            credentials, max_results=max_results, exclude_ids=exclude_message_ids
        )
    except Exception:
        logger.exception("Gmail fetch failed")
        raise HTTPException(status_code=502, detail="failed to fetch Gmail messages")

    logger.info(
        "gmail fetch complete message_count=%d skipped_reviewed=%d",
        len(messages),
        skipped_reviewed_count,
    )

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

    return GmailCandidatesResponse(
        candidates=candidates, skipped_reviewed_count=skipped_reviewed_count
    )
