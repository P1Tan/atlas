import json
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI

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
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(credentials.to_json())
    TOKEN_PATH.chmod(0o600)


def load_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    data = json.loads(TOKEN_PATH.read_text())
    return Credentials.from_authorized_user_info(data, SCOPES)


def has_credentials() -> bool:
    return TOKEN_PATH.exists()


def clear_credentials() -> None:
    TOKEN_PATH.unlink(missing_ok=True)
