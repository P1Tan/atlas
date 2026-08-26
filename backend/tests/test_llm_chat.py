"""Exercises the real OpenAI chat engine for a genuine multi-turn exchange.

Live API call (small cost, non-deterministic wording), so kept separate from
the fast, deterministic /chat endpoint tests and skipped automatically when
no API key is configured.
"""

import os

import pytest

from app.chat import ChatMessage, OpenAIChatEngine, SYSTEM_PROMPT

pytestmark = pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")


@pytest.fixture(scope="module")
def engine() -> OpenAIChatEngine:
    return OpenAIChatEngine()


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
