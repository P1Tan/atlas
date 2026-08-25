import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.google_auth import build_flow, clear_credentials, has_credentials, save_credentials

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
    _pending_state = None
    code_verifier = _pending_code_verifier
    _pending_code_verifier = None

    flow = build_flow(state=state, code_verifier=code_verifier)
    try:
        flow.fetch_token(code=code)
    except Exception:
        logger.exception("Google OAuth token exchange failed")
        raise HTTPException(status_code=502, detail="failed to complete Google sign-in")

    save_credentials(flow.credentials)
    return HTMLResponse("<p>Gmail connected. You can close this tab and return to Atlas.</p>")


@router.get("/status")
def status() -> dict:
    return {"connected": has_credentials()}


@router.post("/disconnect")
def disconnect() -> dict:
    clear_credentials()
    return {"connected": False}
