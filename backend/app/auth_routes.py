import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.google_auth import build_flow, clear_credentials, has_credentials, save_credentials
from app.supabase_client import AuthenticatedUser, get_current_user

logger = logging.getLogger("atlas.auth")

router = APIRouter(prefix="/auth/google", tags=["auth"])

# Single-user, single-in-flight-login assumption: fine for a personal,
# unverified-app OAuth flow. A real multi-user deployment would need this
# keyed per session instead.
_pending_state: Optional[str] = None
# PKCE requires the same code_verifier used to build the authorization URL
# to also be presented at token exchange -- it must survive across these two
# separate requests, not just live inside the Flow object from /login.
_pending_code_verifier: Optional[str] = None


@router.get("/login")
def login() -> RedirectResponse:
    global _pending_state, _pending_code_verifier
    flow = build_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _pending_state = state
    _pending_code_verifier = flow.code_verifier
    return RedirectResponse(authorization_url)


@router.get("/callback")
def callback(code: str, state: str) -> HTMLResponse:
    global _pending_state, _pending_code_verifier
    if state != _pending_state:
        raise HTTPException(status_code=400, detail="invalid OAuth state")

    # Found live (bug audit): _pending_state/_pending_code_verifier used to
    # be cleared BEFORE flow.fetch_token -- the actual network call to
    # Google, and the step most likely to fail transiently (a blip, a
    # timeout). If it failed, the state needed to validate a retry of this
    # same callback was already gone, so a client retrying the identical
    # callback URL (a normal thing to do after a 502) got "invalid OAuth
    # state" instead of another shot at the token exchange, forcing a full
    # restart of the whole /login flow. Cleared only after fetch_token
    # actually succeeds, so a transient failure can still be retried.
    flow = build_flow(state=state, code_verifier=_pending_code_verifier)
    try:
        flow.fetch_token(code=code)
    except Exception:
        logger.exception("Google OAuth token exchange failed")
        raise HTTPException(status_code=502, detail="failed to complete Google sign-in")

    _pending_state = None
    _pending_code_verifier = None

    save_credentials(flow.credentials)
    return HTMLResponse("<p>Gmail connected. You can close this tab and return to Atlas.</p>")


@router.get("/status")
def status() -> dict:
    return {"connected": has_credentials()}


@router.post("/disconnect")
def disconnect(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    """Drops the stored Google credential, requiring a fresh OAuth login.

    `user` is unused in the body on purpose: the Google credential is a
    single server-side file (see `google_auth`), not a per-user record, so
    there is nothing to select by caller. The dependency is here because
    this route is destructive -- until now anyone who could reach the port
    could silently disconnect Gmail without presenting a token.

    `/login`, `/callback` and `/status` stay unauthenticated: the first two
    are browser-driven redirects Google itself calls back into, and the
    last only returns a boolean.
    """
    clear_credentials()
    return {"connected": False}
