"""Milestone 7.1/7.2a/7.3 voice pipeline scaffold -- a standalone script, NOT
part of the FastAPI server, that proves the Pipecat + LiveKit plumbing works
end-to-end (STT -> LLM (with the same tools/persona as text chat) -> TTS)
and gives a rough latency read. Not production voice UX.

As of Milestone 7.3, TTS uses Cartesia (the committed provider, chosen
2026-09-01 over the spec's default guess of ElevenLabs Flash for cost),
replacing 7.1's OpenAI TTS placeholder.

As of Milestone 7.2a, STT is no longer performed server-side. The iOS client
(7.2b, on-device Apple Speech, built separately) transcribes speech itself
and sends transcript text plus turn-boundary signals over LiveKit's data-
message channel; `app/voice_transcript_bridge.py`'s `LiveKitTranscriptBridge`
translates those data messages into the same Pipecat frames
(`UserStartedSpeakingFrame`, `InterimTranscriptionFrame`, `TranscriptionFrame`,
`UserStoppedSpeakingFrame`) that `OpenAISTTService` + `SileroVADAnalyzer`
used to produce in 7.1. There is no server-side STT service or VAD analyzer
in this pipeline anymore.

As of Milestone 7.4a, the assistant's reply text is no longer sent to iOS as
audio alone. `app/voice_assistant_reply_bridge.py`'s
`LiveKitAssistantReplyBridge` sits between `llm` and `tts`, accumulates the
LLM's streamed text for each inference round, and republishes the full reply
back over the same LiveKit data-message channel as a
`{"type": "assistant_reply", "text": "..."}` message -- symmetric to, but
distinct from, the `speech_started`/`interim`/`final`/`speech_stopped`
messages iOS sends the other direction (7.2a/7.2b). This lets iOS show the
assistant's turn in a unified chat transcript alongside the spoken audio,
rather than only playing it out loud.

Same milestone, a second small addition:
`app/voice_tool_result_bridge.py`'s `LiveKitToolResultBridge` (positioned
right after `llm`, alongside `LiveKitAssistantReplyBridge`) forwards
`set_reminder`'s tool-call result to iOS as a
`{"type": "tool_result", "name": "set_reminder", "result": {...}}`
message. Text chat's `/chat` response already lets
`ChatViewModel.scheduleAnyReminders(from:)` schedule the actual on-device
local notification client-side -- without this bridge, a voice-requested
reminder would be spoken back confidently but never actually scheduled,
since the voice pipeline runs tools entirely server-side and nothing
else surfaces a tool's result to iOS.

As of Milestone 7.5 (mode continuity, FR9), `app/voice_transcript_bridge.py`'s
`LiveKitTranscriptBridge` also handles a fourth iOS-originated message type,
`{"type": "context_seed", "messages": [...]}`, sent once right after iOS
connects to a voice session and before any real utterance. It translates to
an `LLMMessagesAppendFrame(messages=sanitized, run_llm=False)`, which the
pipeline below needs no additional wiring for: `LiveKitTranscriptBridge`
already sits between `transport.input()` and `user_aggregator`, exactly
where an `LLMMessagesAppendFrame` needs to land to reach the context
aggregator and be added to `context` without triggering an LLM call. This
closes the mode-continuity gap in the other direction from 7.4's
`LiveKitAssistantReplyBridge`/`LiveKitToolResultBridge`: those let a voice
turn's results flow back into iOS's unified `ChatViewModel.messages`
(voice->text always worked, since text's `/chat` calls resend the full
array); this lets prior text-chat history flow into a voice session's
previously-empty-at-startup LLM context (text->voice), so switching from
typing to voice mid-conversation no longer starts the assistant's voice-side
memory from scratch.

As of Milestone 9.1 (NFR2, reliability), a provider-level pipeline failure
(the LLM or TTS API call itself failing -- rate limit, quota, connectivity,
etc.) is no longer silent. Pipecat's own services already push an
`ErrorFrame` upstream when this happens (confirmed via the installed source,
`services/openai/base_llm.py`); `worker.event_handler("on_pipeline_error")`
below catches it and forwards a fixed, generic
`{"type": "pipeline_error", "message": "..."}` message to iOS -- distinct
from a tool call failing (`ErrorCategory.APPLICATION`), which Pipecat's own
function-call runner already recovers from gracefully (feeds a synthetic
error result back to the model, which explains it in a normal
`assistant_reply`) and this handler deliberately ignores. Before this,
a provider failure meant total silence from the backend: iOS had no way to
know anything had gone wrong until its own 30s client-side safety net
(`VoiceSessionController.awaitingReplyTimeout`) gave up and reverted to idle
with no explanation -- exactly the "silent hang" NFR2 rules out.

There is no LiveKit agent auto-dispatch in Pipecat (confirmed: no such
feature exists or is planned), so this script owns joining the room itself,
like any other participant -- run it with `python -m app.voice_agent` while
a human joins the same room from a browser (see README.md).

Run: python -m app.voice_agent
"""

import asyncio
import logging
from datetime import datetime

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import ErrorFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.livekit import generate_token_with_agent
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.livekit.transport import (
    LiveKitOutputTransportMessageUrgentFrame,
    LiveKitParams,
    LiveKitTransport,
)
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.chat import PERSONA, build_system_prompt
from app.config import (
    CARTESIA_API_KEY,
    CARTESIA_VOICE_ID,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    VOICE_DEV_ROOM_NAME,
    VOICE_DEV_TIMEZONE,
)
from app.extraction import get_default_extractor
from app.memory import get_default_memory_store
from app.tools import build_tools
from app.voice_assistant_reply_bridge import LiveKitAssistantReplyBridge
from app.voice_pipeline_error_bridge import pipeline_error_notification
from app.voice_tool_result_bridge import LiveKitToolResultBridge
from app.voice_tools import to_function_schemas
from app.voice_transcript_bridge import LiveKitTranscriptBridge
from app.weather import get_default_weather_client
from app.web_search import get_default_web_search_client

# Without this, every logger.info() below is silently dropped -- the root
# logger defaults to WARNING (see app/main.py, which does the same).
logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("atlas.voice")

# Milestone 7.1 scaffold only: a real per-session room/token issuance flow
# (via a /chat-style authenticated endpoint) is deferred until the iOS app
# actually initiates voice sessions (7.2+). This is a fixed, known low-stakes
# Supabase UI-test account already used throughout this codebase's iOS test
# suite and prior live verification -- not a new one created for this task.
VOICE_DEV_USER_ID = "325c07ec-8e45-49a1-931e-d29a40ddffce"


def _require_voice_config() -> None:
    missing = [
        name
        for name, value in (
            ("LIVEKIT_URL", LIVEKIT_URL),
            ("LIVEKIT_API_KEY", LIVEKIT_API_KEY),
            ("LIVEKIT_API_SECRET", LIVEKIT_API_SECRET),
            ("CARTESIA_API_KEY", CARTESIA_API_KEY),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required env var(s) for the voice agent scaffold: "
            f"{', '.join(missing)}. Set them in backend/.env (see .env.example)."
        )


async def main() -> None:
    _require_voice_config()

    token = generate_token_with_agent(
        room_name=VOICE_DEV_ROOM_NAME,
        participant_name="atlas-voice-agent",
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    logger.info(
        "atlas voice agent scaffold: joining LiveKit room '%s' at %s",
        VOICE_DEV_ROOM_NAME,
        LIVEKIT_URL,
    )

    transport = LiveKitTransport(
        url=LIVEKIT_URL,
        token=token,
        room_name=VOICE_DEV_ROOM_NAME,
        # audio_in_enabled=False: STT moved on-device (iOS, Milestone 7.2b) --
        # the server no longer needs raw incoming audio, only the data-channel
        # transcript messages that app/voice_transcript_bridge.py translates
        # into Pipecat frames. audio_out_enabled stays True: TTS output audio
        # still flows to the client as before.
        params=LiveKitParams(audio_in_enabled=False, audio_out_enabled=True),
    )

    llm = OpenAILLMService(
        settings=OpenAILLMService.Settings(system_instruction=build_system_prompt(PERSONA))
    )
    tts = CartesiaTTSService(
        api_key=CARTESIA_API_KEY,
        settings=CartesiaTTSService.Settings(voice=CARTESIA_VOICE_ID, model="sonic-3.5"),
    )

    # Scaffold simplification, deliberate: reference_datetime/user identity
    # are captured once at process startup, not per-turn -- unlike the text
    # /chat path, where every HTTP request gets its own fresh
    # reference_datetime. This script is meant to be started right before a
    # test session, not left running for extended periods -- the longer it
    # runs before a human actually joins and talks to it, the more stale
    # today's date/timezone becomes for tools like set_reminder. Restart the
    # script if you're coming back to test it after a break.
    #
    # get_default_weather_client()/get_default_web_search_client()/
    # get_default_memory_store() are also held for the whole process
    # lifetime here, diverging from those functions' own "construct fresh,
    # no caching" convention (see app/supabase_client.py) -- deliberate for
    # this single-process scaffold (their underlying httpx/OpenAI clients
    # are safe for concurrent use), not an oversight.
    tools = build_tools(
        reference_datetime=datetime.now(),
        timezone=VOICE_DEV_TIMEZONE,
        extractor=get_default_extractor(),
        weather_client=get_default_weather_client(),
        search_client=get_default_web_search_client(),
        user_id=VOICE_DEV_USER_ID,
        memory_store=get_default_memory_store(),
    )
    function_schemas: list[FunctionSchema] = to_function_schemas(tools)

    context = LLMContext(tools=function_schemas)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            LiveKitTranscriptBridge(),
            user_aggregator,
            llm,
            LiveKitToolResultBridge(),
            LiveKitAssistantReplyBridge(),
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    @worker.event_handler("on_pipeline_error")
    async def _on_pipeline_error(worker: PipelineWorker, frame: ErrorFrame) -> None:
        notification = pipeline_error_notification(frame)
        if notification is None:
            return
        logger.error("voice pipeline error (category=%s): %s", frame.category, frame.error)
        # queue_frame pushes from the beginning of the pipeline; the Urgent
        # variant is a SystemFrame, sent immediately rather than queued
        # behind whatever's jammed given the pipeline just errored.
        await worker.queue_frame(LiveKitOutputTransportMessageUrgentFrame(message=notification))

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
