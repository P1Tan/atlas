import asyncio

from pipecat.frames.frames import FunctionCallResultFrame, TextFrame
from pipecat.tests.utils import run_test
from pipecat.transports.livekit.transport import LiveKitOutputTransportMessageFrame

from app.voice_tool_result_bridge import LiveKitToolResultBridge


def _result_frame(function_name: str, result) -> FunctionCallResultFrame:
    return FunctionCallResultFrame(
        function_name=function_name,
        tool_call_id="call-1",
        arguments={},
        result=result,
    )


def test_set_reminder_result_publishes_tool_result_message() -> None:
    reminder_result = {"ok": True, "title": "Call the dentist", "trigger_time": "2026-09-05T10:00:00-04:00"}
    down, _ = asyncio.run(
        run_test(
            LiveKitToolResultBridge(),
            frames_to_send=[_result_frame("set_reminder", reminder_result)],
            expected_down_frames=[
                FunctionCallResultFrame,
                LiveKitOutputTransportMessageFrame,
            ],
        )
    )
    # The original frame passes through unchanged.
    assert isinstance(down[0], FunctionCallResultFrame)
    assert down[0].function_name == "set_reminder"
    assert down[0].result == reminder_result

    published = down[1]
    assert isinstance(published, LiveKitOutputTransportMessageFrame)
    assert published.message == {
        "type": "tool_result",
        "name": "set_reminder",
        "result": reminder_result,
    }


def test_other_tool_results_pass_through_without_publishing() -> None:
    for function_name in ("remember_fact", "get_weather", "web_search", "extract_calendar_events"):
        down, _ = asyncio.run(
            run_test(
                LiveKitToolResultBridge(),
                frames_to_send=[_result_frame(function_name, {"ok": True})],
                expected_down_frames=[FunctionCallResultFrame],
            )
        )
        assert len(down) == 1
        assert not any(isinstance(f, LiveKitOutputTransportMessageFrame) for f in down)


def test_unrelated_frame_passes_through_unchanged() -> None:
    down, _ = asyncio.run(
        run_test(
            LiveKitToolResultBridge(),
            frames_to_send=[TextFrame(text="not a tool result")],
            expected_down_frames=[TextFrame],
        )
    )
    assert len(down) == 1
    assert down[0].text == "not a tool result"
    assert not any(isinstance(f, LiveKitOutputTransportMessageFrame) for f in down)
