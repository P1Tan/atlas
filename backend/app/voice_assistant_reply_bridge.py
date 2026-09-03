"""Milestone 7.4a -- publishes the assistant's full spoken reply back to iOS
as TEXT over the same LiveKit data-message channel iOS already uses to send
transcripts to the backend (see app/voice_transcript_bridge.py). Milestones
7.1-7.3 send the reply as AUDIO ONLY (LLM -> TTS -> LiveKit audio track); the
spec requires voice and text turns to share one visible chat transcript on
iOS, and requires the text to be shown even in voice mode, so the reply text
also needs to reach the client independently of the audio.

The mechanism is the mirror image of `voice_transcript_bridge.py`: that
bridge turns iOS-originated data messages into Pipecat frames flowing
downstream through the LLM; this one taps the LLM's own text output and
turns it into a data message flowing further downstream to iOS. Concretely,
`OpenAILLMService` (like any Pipecat LLM service) emits, per inference
round, `LLMFullResponseStartFrame`, zero or more `LLMTextFrame` chunks (each
carrying a piece of `.text`), then `LLMFullResponseEndFrame`. This processor
sits between `llm` and `tts` in the pipeline (see app/voice_agent.py),
accumulates the `LLMTextFrame` chunks between start/end, and -- if any text
was actually said (a pure tool-calling round with no spoken reply
accumulates nothing) -- pushes one additional
`LiveKitOutputTransportMessageFrame(message={"type": "assistant_reply",
"text": ...})` right after the end frame. No reference to the transport
object is needed: pushing the frame downstream is enough, since
`transport.output()` (further down the same pipeline) sends any
`LiveKitOutputTransportMessageFrame` it receives as a real LiveKit data
message, and `tts` (positioned between this bridge and `transport.output()`)
only interacts with `TextFrame`/`TTSAudioRawFrame` types and passes anything
else through unchanged.

`{"type": "assistant_reply", ...}` is deliberately a different `type` string
than iOS's own `speech_started`/`interim`/`final`/`speech_stopped` messages
(see voice_transcript_bridge.py) -- symmetric in shape, but distinct enough
that a client checking `type` alone (not sender/direction) can never confuse
"this is what I said" with "this is what the assistant said."

Chunks are concatenated raw, with no extra separator inserted between them.
`LLMTextFrame` documents (`includes_inter_frame_spaces`, see
pipecat/frames/frames.py) that LLM services already include any necessary
inter-frame spaces in the text they emit -- inserting our own separator
would risk doubling up whitespace the LLM already produced.
"""

import logging

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.livekit.transport import LiveKitOutputTransportMessageFrame

# Matches app/voice_agent.py's logger name -- one logger for the whole voice
# scaffold, not a new one per module.
logger = logging.getLogger("atlas.voice")

# Unlike remember_fact's fact_text (capped at 500 chars, app/tools.py) or the
# transcript bridge's incoming text (capped at 2000, app/voice_transcript_bridge.py),
# nothing else bounds the LLM's free-form reply text before it's published as
# one data message -- cap it here too, for the same reason (a pathologically
# long generation shouldn't become an unbounded single packet; LiveKit itself
# caps a data payload at 15KB, and this stays comfortably under that even
# accounting for multi-byte UTF-8 and JSON escaping overhead).
_MAX_REPLY_LENGTH = 3000


class LiveKitAssistantReplyBridge(FrameProcessor):
    """Taps the LLM's text stream and republishes the full reply to iOS.

    Every other frame type passes through unchanged, including the
    `LLMFullResponseStartFrame`/`LLMTextFrame`/`LLMFullResponseEndFrame`
    frames themselves -- this is a tap, not a filter: nothing this processor
    triggers on is ever swallowed, and it only ever adds one extra frame
    (right after `LLMFullResponseEndFrame`), never removes one. Matches on
    `LLMTextFrame` specifically (not the broader `TextFrame` base class):
    it's the precise type the LLM service itself emits at this position in
    the pipeline (between `llm` and `tts`), and its subclass
    `VisionTextFrame` is still caught by the same `isinstance` check. Other
    `TextFrame` subclasses that exist in pipecat (e.g. `TranscriptionFrame`,
    `InterimTranscriptionFrame`) are produced upstream of `llm`, never
    between `llm` and `tts`, so matching narrowly does not miss anything
    that can actually reach this processor.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._accumulated_text = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._accumulated_text = ""
            await self.push_frame(frame, direction)
        elif isinstance(frame, LLMTextFrame):
            self._accumulated_text += frame.text
            await self.push_frame(frame, direction)
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self.push_frame(frame, direction)
            reply_text = self._accumulated_text.strip()
            if reply_text:
                if len(reply_text) > _MAX_REPLY_LENGTH:
                    logger.warning(
                        "LiveKitAssistantReplyBridge: reply text (%d chars) exceeds "
                        "%d, truncating before publishing",
                        len(reply_text),
                        _MAX_REPLY_LENGTH,
                    )
                    reply_text = reply_text[:_MAX_REPLY_LENGTH]
                await self.push_frame(
                    LiveKitOutputTransportMessageFrame(
                        # Broadcast to every room participant (no
                        # participant_id target) -- the same shared-dev-room
                        # exposure already accepted in 7.2a/7.2b's security
                        # reviews, not a new boundary. The TTS audio for this
                        # same reply is already broadcast room-wide too, so
                        # this doesn't widen who can hear/read the content,
                        # only how mechanically easy it is to read (no STT
                        # needed).
                        message={"type": "assistant_reply", "text": reply_text}
                    ),
                    direction,
                )
            self._accumulated_text = ""
        else:
            await self.push_frame(frame, direction)
