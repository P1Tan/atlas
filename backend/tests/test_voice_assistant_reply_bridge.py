import asyncio

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
)
from pipecat.tests.utils import run_test
from pipecat.transports.livekit.transport import LiveKitOutputTransportMessageFrame

from app.voice_assistant_reply_bridge import LiveKitAssistantReplyBridge


def test_full_response_publishes_concatenated_reply_after_end_frame() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitAssistantReplyBridge(),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="Hello"),
                LLMTextFrame(text=" there"),
                LLMFullResponseEndFrame(),
            ],
            expected_down_frames=[
                LLMFullResponseStartFrame,
                LLMTextFrame,
                LLMTextFrame,
                LLMFullResponseEndFrame,
                LiveKitOutputTransportMessageFrame,
            ],
        )
    )
    # The original four frames pass through unchanged.
    assert isinstance(down[0], LLMFullResponseStartFrame)
    assert isinstance(down[1], LLMTextFrame) and down[1].text == "Hello"
    assert isinstance(down[2], LLMTextFrame) and down[2].text == " there"
    assert isinstance(down[3], LLMFullResponseEndFrame)

    # Raw concatenation, no inserted separator: LLMTextFrame chunks already
    # include any necessary inter-frame spaces themselves
    # (includes_inter_frame_spaces=True in pipecat/frames/frames.py), so
    # "Hello" + " there" == "Hello there", not "Hello  there" or "Hello,there".
    reply = down[4]
    assert isinstance(reply, LiveKitOutputTransportMessageFrame)
    assert reply.message == {"type": "assistant_reply", "text": "Hello there"}


def test_empty_response_publishes_nothing_extra() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitAssistantReplyBridge(),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMFullResponseEndFrame(),
            ],
            expected_down_frames=[
                LLMFullResponseStartFrame,
                LLMFullResponseEndFrame,
            ],
        )
    )
    assert len(down) == 2
    assert not any(isinstance(f, LiveKitOutputTransportMessageFrame) for f in down)


def test_second_response_does_not_leak_text_from_first() -> None:
    # Both rounds carry real text -- round 1's "First reply" must not appear
    # anywhere in round 2's published message. (An earlier version of this
    # test used an empty first round, which only proved a *textless* round
    # stays silent, not that the accumulator actually resets between two
    # worded rounds.)
    down, _ = asyncio.run(
        run_test(
            LiveKitAssistantReplyBridge(),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="First reply."),
                LLMFullResponseEndFrame(),
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="Sure, done."),
                LLMFullResponseEndFrame(),
            ],
            expected_down_frames=[
                LLMFullResponseStartFrame,
                LLMTextFrame,
                LLMFullResponseEndFrame,
                LiveKitOutputTransportMessageFrame,
                LLMFullResponseStartFrame,
                LLMTextFrame,
                LLMFullResponseEndFrame,
                LiveKitOutputTransportMessageFrame,
            ],
        )
    )
    reply_frames = [f for f in down if isinstance(f, LiveKitOutputTransportMessageFrame)]
    assert len(reply_frames) == 2
    assert reply_frames[0].message == {"type": "assistant_reply", "text": "First reply."}
    assert reply_frames[1].message == {"type": "assistant_reply", "text": "Sure, done."}


def test_reply_longer_than_max_length_is_truncated() -> None:
    long_text = "x" * 5000
    down, _ = asyncio.run(
        run_test(
            LiveKitAssistantReplyBridge(),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text=long_text),
                LLMFullResponseEndFrame(),
            ],
            expected_down_frames=[
                LLMFullResponseStartFrame,
                LLMTextFrame,
                LLMFullResponseEndFrame,
                LiveKitOutputTransportMessageFrame,
            ],
        )
    )
    reply_frames = [f for f in down if isinstance(f, LiveKitOutputTransportMessageFrame)]
    assert len(reply_frames[0].message["text"]) == 3000


def test_unrelated_text_frame_passes_through_and_is_not_accumulated() -> None:
    # A plain TextFrame (not an LLMTextFrame) is a safe stand-in for "any
    # other frame type" -- confirms the bridge matches narrowly on
    # LLMTextFrame, not the broader TextFrame base class, and that a
    # non-matching frame in between a start/end pair is not swallowed or
    # treated as part of the reply.
    down, _ = asyncio.run(
        run_test(
            LiveKitAssistantReplyBridge(),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                TextFrame(text="not from the LLM"),
                LLMFullResponseEndFrame(),
            ],
            expected_down_frames=[
                LLMFullResponseStartFrame,
                TextFrame,
                LLMFullResponseEndFrame,
            ],
        )
    )
    assert len(down) == 3
    assert isinstance(down[1], TextFrame)
    assert down[1].text == "not from the LLM"
    assert not any(isinstance(f, LiveKitOutputTransportMessageFrame) for f in down)
