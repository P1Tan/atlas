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
    {"type": "context_seed", "messages": [...]}    -> LLMMessagesAppendFrame(run_llm=False)

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

Milestone 7.5 (mode continuity, FR9) adds a fourth message type:

    {"type": "context_seed", "messages": [{"role": "...", "content": "..."}]}

iOS sends this once, right after connecting to a voice session and before
any real utterance, carrying recent text-chat history so that switching from
text to voice mid-conversation doesn't start the backend's LLM context from
scratch (voice->text already works without any change here, since
`ChatViewModel.messages` is the one shared array both modes append to and
text's `/chat` calls always resend the full array). `messages` is sanitized
the same way `text`/`type` are trusted-but-verified above: each entry must be
a dict, `role` must be exactly `"user"` or `"assistant"` (never `"system"`/
`"tool"` -- this bridge must not let the client inject a fake system prompt
via this path), and `content` must be a non-empty string after stripping
(each capped at `_MAX_TEXT_LENGTH`, same as `final`/`interim` text). The
sanitized list is further capped to the most recent `_MAX_SEED_MESSAGES`
entries. If anything survives sanitization, it is pushed downstream as a
single `LLMMessagesAppendFrame(messages=sanitized, run_llm=False)` --
`run_llm=False` means the messages are added to the context aggregator's
history without triggering an LLM call (confirmed in the installed pipecat
1.8.1 source, `LLMUserContextAggregator._handle_llm_messages_append`). If
nothing survives sanitization, nothing is pushed, matching this bridge's
existing "nothing to do" behavior for the other message types.
"""

import logging

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    LLMMessagesAppendFrame,
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

# A "recent text-chat history" seed has no business being unbounded --
# iOS is expected to already trim to a reasonable recency window (see
# VoiceSessionController.swift), but the bridge caps independently rather
# than trusting that always holds. 20 matches iOS's own cap.
_MAX_SEED_MESSAGES = 20


class LiveKitTranscriptBridge(FrameProcessor):
    """Turns iOS-originated LiveKit data messages into Pipecat frames.

    Every other frame type passes through unchanged. A
    `LiveKitInputTransportMessageFrame` is never forwarded itself -- it is
    consumed and replaced by whichever frame (if any) its already-parsed
    dict payload maps to (LiveKitTransport parses the incoming JSON itself
    before this frame is ever constructed -- see the comment in
    process_frame). A non-dict payload, a missing `"type"` key, an
    unrecognized `"type"` value, or a `"context_seed"` message whose
    `"messages"` field isn't a list are logged and dropped silently; they
    never crash the pipeline and never produce a downstream frame. A
    `"context_seed"` message whose `"messages"` sanitizes down to an empty
    list (e.g. everything filtered out) is dropped silently too, without a
    warning -- that's a legitimate "nothing to seed" case, not malformed
    input.
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
        elif message_type == "context_seed":
            raw_messages = payload.get("messages")
            if not isinstance(raw_messages, list):
                logger.warning(
                    "LiveKitTranscriptBridge: dropping context_seed message with "
                    "missing/non-list 'messages' field: %r",
                    payload,
                )
                return

            sanitized: list[dict] = []
            for item in raw_messages:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                stripped = content.strip()
                if not stripped:
                    continue
                sanitized.append({"role": role, "content": stripped[:_MAX_TEXT_LENGTH]})

            sanitized = sanitized[-_MAX_SEED_MESSAGES:]
            if sanitized:
                await self.push_frame(
                    LLMMessagesAppendFrame(messages=sanitized, run_llm=False),
                    direction,
                )
            return
        else:
            logger.warning(
                "LiveKitTranscriptBridge: dropping data message with missing/"
                "unrecognized type: %r",
                payload,
            )
            return
