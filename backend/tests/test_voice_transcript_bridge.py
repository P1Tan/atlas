import asyncio
from typing import Any

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import run_test
from pipecat.transports.livekit.transport import LiveKitInputTransportMessageFrame

from app.voice_transcript_bridge import LiveKitTranscriptBridge

# LiveKitTransport parses incoming JSON itself before constructing this
# frame (see _on_data_received in the installed pipecat source) -- real
# frames always carry an already-parsed dict in `.message`, never a raw
# JSON string. These tests construct frames the same way, so they actually
# exercise the real production shape rather than one the transport never
# produces.


def _message_frame(message: Any, participant_id: str | None = "p1") -> LiveKitInputTransportMessageFrame:
    return LiveKitInputTransportMessageFrame(message=message, participant_id=participant_id)


def test_speech_started_produces_user_started_speaking_frame() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[_message_frame({"type": "speech_started"})],
            expected_down_frames=[UserStartedSpeakingFrame],
        )
    )
    assert isinstance(down[0], UserStartedSpeakingFrame)


def test_interim_produces_interim_transcription_frame_with_correct_fields() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[
                _message_frame({"type": "interim", "text": "hello wor"}, participant_id="p1")
            ],
            expected_down_frames=[InterimTranscriptionFrame],
        )
    )
    frame = down[0]
    assert isinstance(frame, InterimTranscriptionFrame)
    assert frame.text == "hello wor"
    assert frame.user_id == "p1"


def test_final_produces_finalized_transcription_frame_with_correct_fields() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[
                _message_frame({"type": "final", "text": "hello world"}, participant_id="p1")
            ],
            expected_down_frames=[TranscriptionFrame],
        )
    )
    frame = down[0]
    assert isinstance(frame, TranscriptionFrame)
    assert frame.text == "hello world"
    assert frame.user_id == "p1"
    assert frame.finalized is True


def test_final_text_is_capped_at_max_length() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[_message_frame({"type": "final", "text": "x" * 5000})],
            expected_down_frames=[TranscriptionFrame],
        )
    )
    assert len(down[0].text) == 2000


def test_speech_stopped_produces_user_stopped_speaking_frame() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[_message_frame({"type": "speech_stopped"})],
            expected_down_frames=[UserStoppedSpeakingFrame],
        )
    )
    assert isinstance(down[0], UserStoppedSpeakingFrame)


def test_realistic_sequence_produces_frames_in_order() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[
                _message_frame({"type": "speech_started"}),
                _message_frame({"type": "interim", "text": "hello"}),
                _message_frame({"type": "interim", "text": "hello world"}),
                _message_frame({"type": "final", "text": "hello world."}),
                _message_frame({"type": "speech_stopped"}),
            ],
            expected_down_frames=[
                UserStartedSpeakingFrame,
                InterimTranscriptionFrame,
                InterimTranscriptionFrame,
                TranscriptionFrame,
                UserStoppedSpeakingFrame,
            ],
        )
    )
    assert [f.text for f in down[1:3]] == ["hello", "hello world"]
    assert down[3].text == "hello world."
    assert down[3].finalized is True


def test_non_dict_payload_produces_no_downstream_frames_and_does_not_raise() -> None:
    # Defense-in-depth: LiveKitTransport itself already filters non-dict/
    # unparseable payloads before constructing this frame, but the bridge
    # guards independently rather than assuming that always holds.
    asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[_message_frame("not a dict")],
            expected_down_frames=[],
        )
    )


def test_missing_type_key_produces_no_downstream_frames() -> None:
    asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[_message_frame({"text": "oops"})],
            expected_down_frames=[],
        )
    )


def test_unrecognized_type_produces_no_downstream_frames() -> None:
    asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[_message_frame({"type": "bogus"})],
            expected_down_frames=[],
        )
    )


def test_non_livekit_message_frame_passes_through_unchanged() -> None:
    # A TextFrame is an ordinary DataFrame unrelated to LiveKit data
    # messages -- a safe, innocuous stand-in for "any other frame type"
    # to prove the bridge only intercepts LiveKitInputTransportMessageFrame
    # and otherwise passes everything else straight through.
    down, _ = asyncio.run(
        run_test(
            LiveKitTranscriptBridge(),
            frames_to_send=[TextFrame(text="hello")],
            expected_down_frames=[TextFrame],
        )
    )
    assert down[0].text == "hello"
