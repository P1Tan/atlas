import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Sequence, Tuple

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import GMAIL_LOOKBACK_DAYS

# Guard against a pathologically large message bloating the LLM call.
MAX_BODY_CHARS = 20_000

# Gmail's own per-page ceiling for messages.list -- asking for more is
# silently clamped by the API, so clamp here where it's visible instead.
GMAIL_LIST_MAX_RESULTS = 100


@dataclass
class GmailMessage:
    id: str
    subject: str
    body_text: str


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: List[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def text(self) -> str:
        return " ".join(chunk.strip() for chunk in self._chunks if chunk.strip())


def _strip_html(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.text()


def _decode_part_data(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _find_body_text(payload: dict, prefer_html: bool = False) -> str:
    """Walks a Gmail MIME payload preferring text/plain anywhere in the tree;
    only falls back to text/html (stripped) if no plain-text part exists at
    all, on a second pass."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if body_data:
        if mime_type == "text/plain":
            return _decode_part_data(body_data)
        if mime_type == "text/html" and prefer_html:
            return _strip_html(_decode_part_data(body_data))

    for part in payload.get("parts") or []:
        text = _find_body_text(part, prefer_html=False)
        if text:
            return text

    if not prefer_html:
        for part in payload.get("parts") or []:
            text = _find_body_text(part, prefer_html=True)
            if text:
                return text

    return ""


def _header(headers: List[dict], name: str) -> Optional[str]:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


def fetch_recent_unread_messages(
    credentials: Credentials,
    max_results: int,
    exclude_ids: Sequence[str] = (),
) -> Tuple[List[GmailMessage], int]:
    """Recent AND unread, per the email-privacy invariant -- unread mail from
    years ago is not "recent" just because it's unread. The lookback window
    is a server-enforced policy (app/config.py), not something a caller can
    widen via this function's arguments.

    exclude_ids are messages the caller has already reviewed. Found live:
    Atlas never marks mail read or labels it (readonly scope), and nothing
    server-side records what it already processed, so every "Check Gmail"
    re-offered the same unread messages and the user kept creating duplicate
    calendar events. The caller (iOS) owns that tracking; here the ids are
    dropped before the per-message messages.get, so reviewed mail costs
    neither a body fetch nor an LLM extraction.

    Returns (messages, skipped_count) where skipped_count is how many listed
    messages were dropped for being in exclude_ids -- the caller surfaces
    that so a silently-empty result is explainable.
    """
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    excluded = set(exclude_ids)
    # Over-fetch by the exclusion count: Gmail returns newest first, so
    # without this a full page of already-reviewed mail would push every
    # genuinely new message off the end of the listing.
    list_max_results = min(max_results + len(excluded), GMAIL_LIST_MAX_RESULTS)

    query = f"is:unread newer_than:{GMAIL_LOOKBACK_DAYS}d"
    list_response = (
        service.users().messages().list(userId="me", q=query, maxResults=list_max_results).execute()
    )
    stubs = list_response.get("messages", [])

    skipped_count = 0
    wanted = []
    for stub in stubs:
        if stub.get("id") in excluded:
            skipped_count += 1
            continue
        if len(wanted) < max_results:
            wanted.append(stub)

    messages = []
    for stub in wanted:
        full = service.users().messages().get(userId="me", id=stub["id"], format="full").execute()
        headers = full.get("payload", {}).get("headers", [])
        subject = _header(headers, "Subject") or "(no subject)"
        body_text = _find_body_text(full.get("payload", {}))[:MAX_BODY_CHARS]
        messages.append(GmailMessage(id=full["id"], subject=subject, body_text=body_text))

    return messages, skipped_count
