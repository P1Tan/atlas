import json
import logging
from datetime import datetime, timezone
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from app.supabase_client import get_supabase_client

logger = logging.getLogger("atlas.gmail")

# Read-only per the email-privacy invariant -- Atlas never needs write access
# to Gmail.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# One row per Supabase user, user_id as the primary key -- see
# migrations/0003_user_google_credentials.sql. This replaced a single shared
# server-side token file (backend/.data/google_token.json): with more than
# one signed-in user the second person to connect Gmail overwrote the first,
# and every user's /gmail/candidates then read whichever inbox happened to
# be stored. Every function here takes a user_id for that reason -- there is
# no such thing as "the" Google credential anymore.
CREDENTIALS_TABLE = "user_google_credentials"


def _client_config() -> dict:
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


def build_flow(state: Optional[str] = None, code_verifier: Optional[str] = None) -> Flow:
    flow = Flow.from_client_config(
        _client_config(), scopes=SCOPES, state=state, code_verifier=code_verifier
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    return flow


def save_credentials(user_id: str, credentials: Credentials) -> None:
    """Store (replacing any existing one) this user's Google credential.

    A single upsert on the user_id primary key, so reconnecting Gmail -- or
    persisting a refreshed access token -- overwrites in place rather than
    accumulating rows. The file-based version this replaced wrote a temp
    file and os.replace'd it so a process killed mid-write couldn't leave
    truncated JSON behind; a one-row upsert is already atomic, so that
    machinery is gone with nothing to replace it.
    """
    get_supabase_client().table(CREDENTIALS_TABLE).upsert(
        {
            "user_id": user_id,
            # to_json() returns a JSON *string*; the column is jsonb, so
            # hand PostgREST the dict itself rather than a string stuffed
            # into a JSON string.
            "credentials": json.loads(credentials.to_json()),
            # `default now()` only fires on insert, and this is usually an
            # update of an existing row.
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id",
    ).execute()


def load_credentials(user_id: str) -> Optional[Credentials]:
    """This user's stored Google credential, or None if there isn't a usable
    one. The .eq("user_id", ...) filter is the sole authorization check --
    the backend uses the service_role client, which bypasses Row Level
    Security, so (exactly as in memory.delete_fact) that filter is the only
    thing standing between one user and another user's mailbox."""
    response = (
        get_supabase_client()
        .table(CREDENTIALS_TABLE)
        .select("credentials")
        .eq("user_id", user_id)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return None
    try:
        return Credentials.from_authorized_user_info(rows[0]["credentials"], SCOPES)
    except Exception:
        # Found live (on the file-based version, and just as true of a
        # malformed stored row): an unwrapped call here surfaced as a bare
        # 500 with no detail, while /auth/google/status kept reporting
        # connected=true regardless (it only checks that the credential
        # exists) -- no signal to the client that reconnecting would fix
        # it. Every caller already treats None as "not connected," so
        # collapsing an undecodable credential into that same,
        # already-handled case is correct, not a swallowed error -- logged
        # so it stays observable, and cleared so has_credentials()/status
        # stop claiming a connection that doesn't actually work. A real DB
        # error is deliberately NOT caught here: that's not "this user
        # isn't connected," and it propagates like every other Supabase
        # failure in the app.
        logger.exception("stored Google credentials are unreadable, clearing them")
        clear_credentials(user_id)
        return None


def has_credentials(user_id: str) -> bool:
    response = (
        get_supabase_client()
        .table(CREDENTIALS_TABLE)
        .select("user_id")
        .eq("user_id", user_id)
        .execute()
    )
    return bool(response.data)


def clear_credentials(user_id: str) -> None:
    """Delete only this user's credential. Filtering on user_id is what
    keeps a disconnect from being a disconnect for everyone."""
    get_supabase_client().table(CREDENTIALS_TABLE).delete().eq("user_id", user_id).execute()
