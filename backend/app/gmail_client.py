import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Guard against a pathologically large message bloating the LLM call.
MAX_BODY_CHARS = 20_000


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


def fetch_recent_unread_messages(credentials: Credentials, max_results: int) -> List[GmailMessage]:
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    list_response = (
        service.users()
        .messages()
        .list(userId="me", q="is:unread", maxResults=max_results)
        .execute()
    )
    stubs = list_response.get("messages", [])

    messages = []
    for stub in stubs:
        full = service.users().messages().get(userId="me", id=stub["id"], format="full").execute()
        headers = full.get("payload", {}).get("headers", [])
        subject = _header(headers, "Subject") or "(no subject)"
        body_text = _find_body_text(full.get("payload", {}))[:MAX_BODY_CHARS]
        messages.append(GmailMessage(id=full["id"], subject=subject, body_text=body_text))

    return messages
