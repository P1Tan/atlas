"""Milestone 7.1/7.2a voice pipeline scaffold -- a standalone script, NOT
part of the FastAPI server, that proves the Pipecat + LiveKit plumbing works
end-to-end (STT -> LLM (with the same tools/persona as text chat) -> TTS)
and gives a rough latency read. Not production voice UX.

As of Milestone 7.2a, STT is no longer performed server-side. The iOS client
(7.2b, on-device Apple Speech, built separately) transcribes speech itself
and sends transcript text plus turn-boundary signals over LiveKit's data-
message channel; `app/voice_transcript_bridge.py`'s `LiveKitTranscriptBridge`
translates those data messages into the same Pipecat frames
(`UserStartedSpeakingFrame`, `InterimTranscriptionFrame`, `TranscriptionFrame`,
`UserStoppedSpeakingFrame`) that `OpenAISTTService` + `SileroVADAnalyzer`
used to produce in 7.1. There is no server-side STT service or VAD analyzer
in this pipeline anymore.

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
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.livekit import generate_token_with_agent
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.chat import PERSONA, build_system_prompt
from app.config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    VOICE_DEV_ROOM_NAME,
    VOICE_DEV_TIMEZONE,
)
from app.extraction import get_default_extractor
from app.memory import get_default_memory_store
from app.tools import build_tools
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


def _require_livekit_config() -> None:
    missing = [
        name
        for name, value in (
            ("LIVEKIT_URL", LIVEKIT_URL),
            ("LIVEKIT_API_KEY", LIVEKIT_API_KEY),
            ("LIVEKIT_API_SECRET", LIVEKIT_API_SECRET),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required LiveKit env var(s) for the voice agent scaffold: "
            f"{', '.join(missing)}. Set them in backend/.env (see .env.example)."
        )


async def main() -> None:
    _require_livekit_config()

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
    tts = OpenAITTSService()

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

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
