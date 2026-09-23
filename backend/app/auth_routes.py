import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.google_auth import build_flow, clear_credentials, has_credentials, save_credentials
from app.supabase_client import AuthenticatedUser, get_current_user

logger = logging.getLogger("atlas.auth")

router = APIRouter(prefix="/auth/google", tags=["auth"])

# How long an authorization URL handed out by /start stays usable: long
# enough to actually complete a consent screen (pick an account, read the
# scopes, approve, maybe re-authenticate to Google first), short enough that
# an abandoned login stops being usable quickly.
OAUTH_STATE_TTL_SECONDS = 600.0


class GoogleAuthStartResponse(BaseModel):
    """The URL the client opens in a browser. A declared model rather than a
    bare dict because this is a new client-facing shape (same reasoning as
    VoiceTokenResponse) -- it puts the contract in the OpenAPI schema the
    iOS side is written against."""

    authorization_url: str


@dataclass
class _PendingLogin:
    """One in-flight login: which user started it, and what /callback needs
    to finish it."""

    user_id: str
    # PKCE requires the same code_verifier used to build the authorization
    # URL to also be presented at token exchange -- it has to survive across
    # two separate HTTP requests, not just live inside the Flow object that
    # /start built and threw away.
    code_verifier: Optional[str]
    expires_at: float


# state -> the login it belongs to. This is what carries the user's identity
# across the browser round trip: Google redirects the user's browser to
# /callback itself, with no bearer token and no session, so the opaque
# `state` parameter we generated is the only thing tying that callback back
# to the user who started the login. It replaced module-level
# _pending_state/_pending_code_verifier globals that could only ever hold
# one login, for one (assumed only) user.
#
# In-process memory, deliberately, on the same terms as app.rate_limit's
# counters: this backend runs as a single uvicorn process bound to
# 127.0.0.1. Running it as multiple workers or instances WOULD break this --
# /start and /callback can then land on different processes and every login
# fails with "invalid OAuth state" -- so this is a thing to fix before
# hosting behind more than one process, not a thing to discover there. The
# fix is a shared short-lived store (Redis, or a Supabase table with the
# same TTL), which is why the entries carry an explicit expiry rather than
# relying on the process being restarted.
_pending_logins: Dict[str, _PendingLogin] = {}
# Sync route functions run in Starlette's thread pool, so two logins really
# can be in flight here at once -- same reasoning as RateLimiter's lock.
# Without it, pruning could iterate the dict while another request mutates
# it.
_pending_logins_lock = threading.Lock()


def _prune_expired_logins(now: float) -> None:
    """Drop timed-out entries. Callers hold _pending_logins_lock.

    Done on every /start and /callback because nothing else ever removes an
    abandoned login (the user closing the consent screen leaves no signal at
    all), and without it the dict would only ever grow.
    """
    for state in [s for s, pending in _pending_logins.items() if pending.expires_at <= now]:
        del _pending_logins[state]


@router.post("/start", response_model=GoogleAuthStartResponse)
def start(user: AuthenticatedUser = Depends(get_current_user)) -> GoogleAuthStartResponse:
    """Begins Google OAuth for the calling user and returns the URL to open.

    Replaces a GET /login that redirected straight to Google. iOS opens the
    consent page with UIApplication.shared.open(url) -- a plain browser
    open, which cannot carry a bearer token -- so a redirect endpoint had no
    way to know which user it was connecting. Authenticating here instead
    and handing the URL back as JSON means the client (which does have a
    token) opens it, and the state parameter carries the user id through the
    round trip to /callback.
    """
    flow = build_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    now = time.monotonic()
    with _pending_logins_lock:
        _prune_expired_logins(now)
        _pending_logins[state] = _PendingLogin(
            user_id=user.id,
            code_verifier=flow.code_verifier,
            expires_at=now + OAUTH_STATE_TTL_SECONDS,
        )
    return GoogleAuthStartResponse(authorization_url=authorization_url)


@router.get("/callback")
def callback(code: str, state: str) -> HTMLResponse:
    """Where Google sends the user's browser after consent.

    Unauthenticated on purpose: Google drives this request and it arrives
    with no bearer token. `state` is what stands in for one -- unguessable,
    issued by /start to one authenticated user, expiring, and consumed by
    the exchange that succeeds -- and looking it up is how we recover whose
    credential this is. (Precisely: it is removed once fetch_token returns,
    so a replayed callback is rejected, but two genuinely simultaneous
    callbacks for the same state can both reach the exchange. Harmless --
    same user either way, and Google only honours the authorization code
    once -- and the alternative, holding the lock across a network call,
    would serialize every login.)
    """
    now = time.monotonic()
    with _pending_logins_lock:
        _prune_expired_logins(now)
        pending = _pending_logins.get(state)
    if pending is None:
        # Unknown, expired, or already used -- deliberately one message, so
        # a caller can't probe which.
        raise HTTPException(status_code=400, detail="invalid OAuth state")

    # Found live (bug audit): the pending state/code_verifier used to be
    # cleared BEFORE flow.fetch_token -- the actual network call to Google,
    # and the step most likely to fail transiently (a blip, a timeout). If
    # it failed, the state needed to validate a retry of this same callback
    # was already gone, so a client retrying the identical callback URL (a
    # normal thing to do after a 502) got "invalid OAuth state" instead of
    # another shot at the token exchange, forcing a full restart of the
    # login. Still removed only after fetch_token actually succeeds, so a
    # transient failure can still be retried.
    flow = build_flow(state=state, code_verifier=pending.code_verifier)
    try:
        flow.fetch_token(code=code)
    except Exception:
        logger.exception("Google OAuth token exchange failed")
        raise HTTPException(status_code=502, detail="failed to complete Google sign-in")

    with _pending_logins_lock:
        _pending_logins.pop(state, None)

    try:
        save_credentials(pending.user_id, flow.credentials)
    except Exception:
        # The one failure path where the user has already granted consent:
        # the authorization code is spent and the state is gone, so there is
        # nothing to retry -- they have to start over. Worth a real message
        # and a log line rather than the bare 500 this would otherwise be,
        # since otherwise nothing anywhere records that a credential was
        # obtained and then dropped.
        logger.exception("failed to store Google credentials after a successful exchange")
        raise HTTPException(status_code=502, detail="failed to store Google credentials")

    return HTMLResponse("<p>Gmail connected. You can close this tab and return to Atlas.</p>")


@router.get("/status")
def status(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    """Whether the CALLING user has Gmail connected.

    Authenticated now that credentials are per user: there is no
    user-independent boolean left to hand out, and answering "is anyone
    connected" is exactly the confusion this change removes.
    """
    return {"connected": has_credentials(user.id)}


@router.post("/disconnect")
def disconnect(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    """Drops the calling user's stored Google credential; reconnecting means
    a fresh consent flow via POST /auth/google/start.

    Scoped to `user.id`, so it clears one row and leaves every other user's
    Gmail connection alone. It is also destructive, which is why it required
    a token even back when the credential was a single shared file that no
    caller could select into.
    """
    clear_credentials(user.id)
    return {"connected": False}
