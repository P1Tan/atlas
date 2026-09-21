import base64
from typing import List
from unittest.mock import MagicMock, patch

from app import gmail_client
from app.gmail_client import _find_body_text, _header, _strip_html, fetch_recent_unread_messages


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _fake_service(stub_ids: List[str]) -> MagicMock:
    """A Gmail service whose messages.list returns the given ids (newest
    first, as Gmail does) and whose messages.get answers with a minimal
    plain-text message for whichever id is asked for."""
    service = MagicMock()
    messages = service.users.return_value.messages.return_value
    messages.list.return_value.execute.return_value = {
        "messages": [{"id": stub_id} for stub_id in stub_ids]
    }

    def _get(userId: str, id: str, format: str) -> MagicMock:
        return MagicMock(
            execute=lambda: {
                "id": id,
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [{"name": "Subject", "value": f"Subject {id}"}],
                    "body": {"data": _b64(f"Body of {id}")},
                },
            }
        )

    messages.get.side_effect = _get
    return service


def _list_kwargs(service: MagicMock) -> dict:
    return service.users.return_value.messages.return_value.list.call_args.kwargs


def _fetched_ids(service: MagicMock) -> List[str]:
    return [
        call.kwargs["id"]
        for call in service.users.return_value.messages.return_value.get.call_args_list
    ]


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

    fake_service = _fake_service([])

    with patch.object(gmail_client, "build", return_value=fake_service) as mock_build:
        messages, skipped = fetch_recent_unread_messages(credentials=MagicMock(), max_results=5)

    mock_build.assert_called_once()
    assert _list_kwargs(fake_service)["q"] == "is:unread newer_than:30d"
    assert _list_kwargs(fake_service)["maxResults"] == 5
    assert (messages, skipped) == ([], 0)


def test_fetch_drops_excluded_messages_and_reports_how_many(monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "GMAIL_LOOKBACK_DAYS", 30)
    fake_service = _fake_service(["msg-1", "msg-2", "msg-3"])

    with patch.object(gmail_client, "build", return_value=fake_service):
        messages, skipped = fetch_recent_unread_messages(
            credentials=MagicMock(), max_results=5, exclude_ids=["msg-1", "msg-3"]
        )

    assert [m.id for m in messages] == ["msg-2"]
    assert skipped == 2


def test_fetch_never_downloads_the_body_of_an_excluded_message(monkeypatch) -> None:
    """The exclusion has to happen before messages.get, not after: fetching a
    reviewed message's body would cost a round trip and an LLM extraction for
    something the user has already dealt with."""
    monkeypatch.setattr(gmail_client, "GMAIL_LOOKBACK_DAYS", 30)
    fake_service = _fake_service(["msg-1", "msg-2"])

    with patch.object(gmail_client, "build", return_value=fake_service):
        fetch_recent_unread_messages(
            credentials=MagicMock(), max_results=5, exclude_ids=["msg-1"]
        )

    assert _fetched_ids(fake_service) == ["msg-2"]


def test_fetch_over_fetches_so_excluded_mail_cannot_crowd_out_new_mail(monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "GMAIL_LOOKBACK_DAYS", 30)
    fake_service = _fake_service([])

    with patch.object(gmail_client, "build", return_value=fake_service):
        fetch_recent_unread_messages(
            credentials=MagicMock(), max_results=10, exclude_ids=[f"msg-{i}" for i in range(7)]
        )

    assert _list_kwargs(fake_service)["maxResults"] == 17


def test_fetch_over_fetch_is_capped_at_the_gmail_page_limit(monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "GMAIL_LOOKBACK_DAYS", 30)
    fake_service = _fake_service([])

    with patch.object(gmail_client, "build", return_value=fake_service):
        fetch_recent_unread_messages(
            credentials=MagicMock(), max_results=20, exclude_ids=[f"msg-{i}" for i in range(200)]
        )

    assert _list_kwargs(fake_service)["maxResults"] == gmail_client.GMAIL_LIST_MAX_RESULTS


def test_fetch_returns_at_most_max_results_after_filtering(monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "GMAIL_LOOKBACK_DAYS", 30)
    fake_service = _fake_service(["msg-1", "msg-2", "msg-3", "msg-4", "msg-5"])

    with patch.object(gmail_client, "build", return_value=fake_service):
        messages, skipped = fetch_recent_unread_messages(
            credentials=MagicMock(), max_results=2, exclude_ids=["msg-2"]
        )

    assert [m.id for m in messages] == ["msg-1", "msg-3"]
    assert skipped == 1
    assert _fetched_ids(fake_service) == ["msg-1", "msg-3"]
