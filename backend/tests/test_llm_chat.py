"""Exercises the real OpenAI chat engine for a genuine multi-turn exchange.

Live API call (small cost, non-deterministic wording), so kept separate from
the fast, deterministic /chat endpoint tests and skipped automatically when
no API key is configured.
"""

import os
from datetime import datetime
from typing import List

import pytest

from app.chat import ChatMessage, OpenAIChatEngine, SYSTEM_PROMPT
from app.extraction import ExtractedEventDraft
from app.tools import build_tools

pytestmark = pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")


@pytest.fixture(scope="module")
def engine() -> OpenAIChatEngine:
    return OpenAIChatEngine()


class _FakeExtractor:
    """Fakes only the extraction LLM call -- everything else (the chat
    model deciding to call the tool, date resolution, ambiguity handling)
    runs for real."""

    model_name = "fake-extractor"

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return [
            ExtractedEventDraft(
                title="Lunch",
                date_phrase="next Tuesday at noon",
                source_excerpt=text,
                confidence="high",
            )
        ]


def test_holds_context_across_two_turns(engine) -> None:
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content="My favorite color is teal. Just acknowledge that."),
    ]
    first_reply = engine.run_turn(messages, tools=[])
    assert first_reply[-1].role == "assistant"
    assert first_reply[-1].content

    messages += first_reply
    messages.append(ChatMessage(role="user", content="What's my favorite color? Answer in one word."))
    second_reply = engine.run_turn(messages, tools=[])

    assert "teal" in (second_reply[-1].content or "").lower()


def test_no_tools_available_still_replies_in_plain_text(engine) -> None:
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content="Say hello in exactly one short sentence."),
    ]
    reply = engine.run_turn(messages, tools=[])

    assert len(reply) == 1
    assert reply[0].role == "assistant"
    assert reply[0].content


def test_model_calls_the_email_to_calendar_tool_when_appropriate(engine) -> None:
    tools = build_tools(datetime(2026, 8, 26, 12, 0), "America/New_York", _FakeExtractor())
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                "Here's an email: 'Let's grab lunch next Tuesday at noon.' "
                "Extract any calendar events from it and tell me what you found."
            ),
        ),
    ]

    reply = engine.run_turn(messages, tools=tools)

    assert any(m.role == "tool" for m in reply), "expected the model to call the tool, not answer directly"
    final_message = reply[-1]
    assert final_message.role == "assistant"
    assert "lunch" in (final_message.content or "").lower()
