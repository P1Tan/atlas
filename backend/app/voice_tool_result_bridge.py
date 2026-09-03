"""Milestone 7.4a-2 -- publishes set_reminder's tool-call result back to iOS
as a data message, so voice-triggered reminders actually get scheduled
on-device the same way text-chat ones do.

Text chat's /chat response already includes tool-role messages in its
new_messages array, and ChatViewModel.scheduleAnyReminders(from:) scans
those for set_reminder results and calls ReminderScheduler.schedule(...)
client-side -- because per this project's own invariant
(assistant-spec.md S11), a reminder isn't done when the LLM "sets" it,
it's done when the phone actually alerts, and only the device can
schedule that alert. The voice pipeline runs tools entirely server-side
inside Pipecat (app/voice_tools.py), so without this bridge, a
voice-requested reminder would be spoken back confidently but never
actually scheduled -- a real gap, not a hypothetical one.

Pipecat's LLM services broadcast a FunctionCallResultFrame (fields:
function_name, tool_call_id, arguments, result) for every tool call's
result, both downstream and upstream (via broadcast_frame -- confirmed in
the installed pipecat source, services/llm_service.py's
function_call_result_callback). This processor sits in the same pipeline
position as LiveKitAssistantReplyBridge (between llm and tts, see
app/voice_agent.py) and taps FunctionCallResultFrame specifically for
function_name == "set_reminder" -- the only tool with a required
client-side side effect; every other tool (remember_fact, search results,
weather, calendar extraction) is either already complete server-side or
purely informational for the model to narrate, needing no client action.
"""

import logging

from pipecat.frames.frames import Frame, FunctionCallResultFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.livekit.transport import LiveKitOutputTransportMessageFrame

# Matches app/voice_agent.py's logger name -- one logger for the whole voice
# scaffold, not a new one per module.
logger = logging.getLogger("atlas.voice")

# The only tool whose result needs a client-side action. Extend this set if
# a future tool gains its own required on-device side effect.
_TOOLS_NEEDING_CLIENT_ACTION = {"set_reminder"}


class LiveKitToolResultBridge(FrameProcessor):
    """Forwards select tool-call results to iOS as data messages.

    Every frame passes through unchanged -- this is a tap, not a filter.
    A `FunctionCallResultFrame` for a tool in `_TOOLS_NEEDING_CLIENT_ACTION`
    also produces one additional `LiveKitOutputTransportMessageFrame`,
    published right after the original frame. Unlike
    `LiveKitAssistantReplyBridge`, there is no accumulation across frames --
    a `FunctionCallResultFrame` is a single, self-contained frame per tool
    call, not chunks to gather.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        await self.push_frame(frame, direction)

        if isinstance(frame, FunctionCallResultFrame) and frame.function_name in _TOOLS_NEEDING_CLIENT_ACTION:
            await self.push_frame(
                LiveKitOutputTransportMessageFrame(
                    message={
                        "type": "tool_result",
                        "name": frame.function_name,
                        "result": frame.result,
                    }
                ),
                direction,
            )
