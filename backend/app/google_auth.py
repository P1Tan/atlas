import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI

logger = logging.getLogger("atlas.gmail")

# Read-only per the email-privacy invariant -- Atlas never needs write access
# to Gmail.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

TOKEN_PATH = Path(__file__).resolve().parent.parent / ".data" / "google_token.json"


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


def save_credentials(credentials: Credentials) -> None:
    # Atomic write (temp file + rename), not a plain write_text() -- found
    # live: a plain write can leave a truncated/invalid JSON file behind if
    # the process is killed mid-write (a deploy restart, OOM, etc. landing
    # at exactly the wrong moment), which load_credentials() previously had
    # no way to recover from short of a manual fix. os.replace on the same
    # filesystem is atomic, so a reader only ever sees the old complete file
    # or the new complete file, never a partial one.
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=TOKEN_PATH.parent, prefix=".google_token_", suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(credentials.to_json())
        tmp_path.chmod(0o600)
        os.replace(tmp_path, TOKEN_PATH)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def load_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
        return Credentials.from_authorized_user_info(data, SCOPES)
    except Exception:
        # Found live: an unwrapped call here surfaced as a bare 500 with no
        # detail on a corrupted token file, while /auth/google/status kept
        # reporting connected=true regardless (it only checks file
        # existence) -- no signal to the client that reconnecting would
        # fix it. Every caller of load_credentials() already treats None as
        # "not connected," so collapsing any unreadable/malformed file into
        # that same, already-handled case is correct, not a swallowed
        # error -- logged so it's still observable, and cleared so
        # has_credentials()/status stop claiming a connection that doesn't
        # actually work.
        logger.exception("stored Google credentials file is unreadable, clearing it")
        clear_credentials()
        return None


def has_credentials() -> bool:
    return TOKEN_PATH.exists()


def clear_credentials() -> None:
    TOKEN_PATH.unlink(missing_ok=True)
