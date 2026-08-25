import base64

from app.gmail_client import _find_body_text, _header, _strip_html


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
