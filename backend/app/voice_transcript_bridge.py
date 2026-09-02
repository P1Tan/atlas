"""Milestone 7.2a -- translates LiveKit data-channel messages sent by the iOS
client (7.2b, on-device Apple Speech) into the Pipecat frames that the rest
of the voice pipeline (see app/voice_agent.py) already understands.

This replaces the server-side STT/VAD stack (`OpenAISTTService` +
`SileroVADAnalyzer`, Milestone 7.1) with a bridge that trusts turn-taking and
transcript decisions the client already made, and forwards them downstream
as native Pipecat frames:

    {"type": "speech_started"}                    -> UserStartedSpeakingFrame
    {"type": "interim", "text": "..."}             -> InterimTranscriptionFrame
    {"type": "final", "text": "..."}               -> TranscriptionFrame(finalized=True)
    {"type": "speech_stopped"}                     -> UserStoppedSpeakingFrame

Paired with `LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies())`
in app/voice_agent.py, pushing `UserStartedSpeakingFrame`/`UserStoppedSpeakingFrame`
directly (rather than `ProposedUserStarted/StoppedSpeakingFrame`) tells the
aggregator the turn boundary is already decided -- it adopts the decision and
emits nothing further, rather than re-deciding it itself.

Trust boundary, unchanged from 7.1 but worth being explicit about: a
`TranscriptionFrame` produced here is NOT backed by any real STT-on-real-audio
provenance the way it was in 7.1 -- it is trusted-by-construction from
whatever data message arrived from whoever is in the LiveKit room (room
membership is the only gate, via the signed join token; there is no
per-message authentication). Anyone who could previously inject text by
speaking it into a mic (7.1) can now do so directly and far more cheaply
(network-speed, no audio needed) -- a widening of ease-of-exploitation, not a
new capability. Known, deliberately deferred for this scaffold stage: no
per-participant rate limiting on incoming messages, so a malicious/
malfunctioning participant could flood "final" messages, each one triggering
a real LLM call with tool access (including `remember_fact` writes to real
Supabase data for the fixed dev user). A `text` length cap is enforced below
as a cheap partial mitigation; real rate limiting is not, and should be
addressed before this is reachable by anything other than a manually-run
developer scaffold.
"""

import logging

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.livekit.transport import LiveKitInputTransportMessageFrame
from pipecat.utils.time import time_now_iso8601

# Matches app/voice_agent.py's logger name -- one logger for the whole voice
# scaffold, not a new one per module.
logger = logging.getLogger("atlas.voice")

# Cheap, partial mitigation against a flood of oversized "final" messages --
# see the trust-boundary note above. Matches the spirit of remember_fact's
# 500-char cap (app/tools.py), not real rate limiting.
_MAX_TEXT_LENGTH = 2000


class LiveKitTranscriptBridge(FrameProcessor):
    """Turns iOS-originated LiveKit data messages into Pipecat frames.

    Every other frame type passes through unchanged. A
    `LiveKitInputTransportMessageFrame` is never forwarded itself -- it is
    consumed and replaced by whichever frame (if any) its already-parsed
    dict payload maps to (LiveKitTransport parses the incoming JSON itself
    before this frame is ever constructed -- see the comment in
    process_frame). A non-dict payload, a missing `"type"` key, or an
    unrecognized `"type"` value are logged and dropped silently; they never
    crash the pipeline and never produce a downstream frame.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not isinstance(frame, LiveKitInputTransportMessageFrame):
            await self.push_frame(frame, direction)
            return

        user_id = frame.participant_id or "unknown"

        # LiveKitTransport (see its _on_data_received) already does
        # json.loads(...) on the raw bytes before ever constructing this
        # frame -- frame.message arrives as an already-parsed dict, not a
        # JSON string. Re-parsing it here would raise TypeError on every
        # real message and silently drop it as "malformed," making this
        # bridge a complete no-op against the real transport. Guard on the
        # already-parsed shape instead of re-decoding it.
        payload = frame.message
        if not isinstance(payload, dict):
            logger.warning(
                "LiveKitTranscriptBridge: dropping non-object data message: %r",
                payload,
            )
            return

        message_type = payload.get("type")
        text = str(payload.get("text", ""))[:_MAX_TEXT_LENGTH]

        if message_type == "speech_started":
            await self.push_frame(UserStartedSpeakingFrame(), direction)
        elif message_type == "interim":
            await self.push_frame(
                InterimTranscriptionFrame(
                    text=text,
                    user_id=user_id,
                    timestamp=time_now_iso8601(),
                ),
                direction,
            )
        elif message_type == "final":
            await self.push_frame(
                TranscriptionFrame(
                    text=text,
                    user_id=user_id,
                    timestamp=time_now_iso8601(),
                    finalized=True,
                ),
                direction,
            )
        elif message_type == "speech_stopped":
            await self.push_frame(UserStoppedSpeakingFrame(), direction)
        else:
            logger.warning(
                "LiveKitTranscriptBridge: dropping data message with missing/"
                "unrecognized type: %r",
                payload,
            )
            return
