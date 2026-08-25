import base64
from unittest.mock import MagicMock, patch

from app import gmail_client
from app.gmail_client import _find_body_text, _header, _strip_html, fetch_recent_unread_messages


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def test_header_is_case_insensitive_and_returns_none_when_missing() -> None:
    headers = [{"name": "Subject", "value": "Lunch?"}]
    assert _header(headers, "subject") == "Lunch?"
    assert _header(headers, "From") is None


def test_strip_html_extracts_readable_text_only() -> None:
    html = "<html><body><p>Hi there,</p><p>See you <b>Thursday</b>.</p></body></html>"
    assert _strip_html(html) == "Hi there, See you Thursday ."


def test_find_body_text_prefers_plain_text_when_directly_present() -> None:
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("Let's meet Thursday at 3pm.")},
    }
    assert _find_body_text(payload) == "Let's meet Thursday at 3pm."


def test_find_body_text_prefers_plain_text_part_over_html_part() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>HTML version</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("Plain version")}},
        ],
    }
    assert _find_body_text(payload) == "Plain version"


def test_find_body_text_falls_back_to_html_when_no_plain_text_exists() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>Only HTML here</p>")}},
        ],
    }
    assert _find_body_text(payload) == "Only HTML here"


def test_find_body_text_recurses_into_nested_multipart() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("Nested plain text")}},
                ],
            },
            {"mimeType": "application/pdf", "body": {"data": _b64("not text")}},
        ],
    }
    assert _find_body_text(payload) == "Nested plain text"


def test_find_body_text_returns_empty_string_when_nothing_usable() -> None:
    payload = {"mimeType": "application/pdf", "body": {}}
    assert _find_body_text(payload) == ""


def test_fetch_query_is_scoped_to_unread_and_recent(monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "GMAIL_LOOKBACK_DAYS", 30)

    fake_service = MagicMock()
    fake_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }

    with patch.object(gmail_client, "build", return_value=fake_service) as mock_build:
        fetch_recent_unread_messages(credentials=MagicMock(), max_results=5)

    mock_build.assert_called_once()
    list_call_kwargs = fake_service.users.return_value.messages.return_value.list.call_args.kwargs
    assert list_call_kwargs["q"] == "is:unread newer_than:30d"
    assert list_call_kwargs["maxResults"] == 5
