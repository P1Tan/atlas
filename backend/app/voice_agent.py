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
feature exists or is planned), so this module owns joining the room itself,
like any other participant.

As of the per-session room fix (bug audit finding, HIGH -- cross-user
privacy leak): every authenticated user used to get a `/voice/token` scoped
to this same fixed `VOICE_DEV_ROOM_NAME`, and this module's own bot only
ever joined that one room too -- any two real accounts would land in the
identical LiveKit room, able to hear each other's live conversation and
(since any room participant can publish LiveKit data messages) potentially
inject spoofed `assistant_reply`/`tool_result` messages into someone else's
session. `run_voice_session(room_name, user_id, timezone)` below is now the
real per-session entry point: `app/voice_routes.py`'s `/voice/token` mints a
fresh, unique room per request and spawns a dedicated `asyncio.Task` running
this function directly in the FastAPI process's own event loop (no more
separate `python -m app.voice_agent` process needed for real app usage) --
true per-session isolation, one bot instance per real conversation, torn
down once that session ends rather than sitting in a shared room forever.

Two later refinements to that per-session shape (review findings F1/F2):
`run_voice_session` takes an optional `ready` event it sets from the
transport's own "on_connected" -- `/voice/token` waits on it so a token is
never handed out for a room this bot hasn't joined yet (LiveKit drops data
messages published before a participant joins, which silently cost real
sessions their `context_seed`/`location`) -- and one session is now capped
at `_MAX_SESSION_LIFETIME_SECS` so no pipeline can outlive its own room by
more than that if every event-driven exit is somehow missed. (That cap was
originally the token's own 1h TTL; see the cost-reduction note below for why
it is now minutes instead.)

This module's own `python -m app.voice_agent` CLI entry point (`main()`,
`_run_dev_session_loop()`) still exists, unchanged in spirit, for manual
standalone testing against the fixed dev room/account -- it is no longer
what real voice traffic runs through, only a convenience for testing this
pipeline in isolation without going through the app end to end.

As of the auto-rejoin fix (found live during on-device testing): LiveKit
Cloud closes a room server-side once every human participant has left, which
disconnects this bot too -- confirmed via the log, `Disconnected from
atlas-dev. Reason: None` / `RoomClosed`, with no reconnection attempt after.
This recurred repeatedly during a single testing session and, from the
tester's side, was indistinguishable from "Atlas is being slow" until the
log was checked directly (the pipeline doesn't error or exit when this
happens -- `WorkerRunner.run()` just never returns, sitting idle in a room
it's no longer part of). `main()` (now via `_run_dev_session_loop()`, split
out by the later per-session room fix) wraps one join+run attempt (now
`run_voice_session`) in a loop; `transport.event_handler("on_disconnected")`
calls `runner.cancel(...)`, which is what makes `run()` actually return so
the loop can build a fresh token/transport/pipeline and rejoin. Building
`tools`/`function_schemas` inside that function (moved from a single
process-startup call) is a deliberate side effect of this change, not
incidental: the module docstring above used to tell testers to restart the
script after a break specifically because `reference_datetime` (baked into
`tools` once at startup) would otherwise go stale across a long-idle
process -- the whole point of auto-rejoin is to make the process long-lived
across breaks, so that staleness had to be fixed here too, not left as a
now-silent version of the same bug.

As of the LiveKit cost-reduction pass (2026-09-18), this module also stops
paying for rooms nobody is using. LiveKit Cloud bills participant connection
minutes, and a session here is ONE voice turn, so every second this bot stays
connected past the end of that turn is pure waste. Three leaks were fixed
(the matching client-side ones live in iOS's `VoiceSessionController`):
the bot used to linger in the room after the human left, waiting for LiveKit
Cloud's own empty-room reaper (~20-35s observed) to trigger
"on_disconnected"; it had no exit at all for a human who never joined (iOS
got a token but failed to connect), so the only backstop was the 1h lifetime
cap; and that cap itself was set to the token's 1h TTL, which for a
one-turn session meant an orphaned bot could bill a full hour. See
`_MAX_SESSION_LIFETIME_SECS`, `_NOBODY_JOINED_TIMEOUT_SECS` and the
participant event handlers in `run_voice_session` below.

Run (manual dev testing against the fixed dev room only -- NOT needed for
real app usage anymore, see the per-session room note above):
python -m app.voice_agent
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

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


# How long to wait before rejoining after a disconnect or an unexpected
# session-level crash. Not exponential backoff -- a fixed, short delay is
# enough for LiveKit Cloud's own room teardown to finish, and this is a
# single human-tester scaffold, not a fleet of bots that could hammer an API
# on a persistent outage.
_RECONNECT_DELAY_SECS = 3

# Hard upper bound on a single session's lifetime (review finding F2), as a
# backstop -- NOT the normal exit path, which is still the participant/
# transport handlers below. Needed because `cancel_on_idle_timeout=False`
# (see below) removes Pipecat's own only other time-based exit: if every
# event-driven exit is missed (a network partition, a transport-level bug),
# the task would otherwise hold an OpenAI + Cartesia websocket pair -- and a
# billed LiveKit participant connection -- open for the rest of the
# process's life.
#
# This used to be 3600, matched to the 1h TTL of the LiveKit token
# `/voice/token` mints, on the reasoning that past that the client couldn't
# rejoin this room anyway. That was the wrong bound to copy: a session here
# is ONE voice turn (listening, bounded client-side by a 45s idle timeout,
# plus at most iOS's 90s `maxAwaitingReplyDuration` wait for the reply), so
# no legitimate session comes anywhere near even 10 minutes -- while an
# orphaned bot billed LiveKit for the full hour. 600s keeps a generous
# multiple of the real worst case and caps the damage at ~10 minutes.
_MAX_SESSION_LIFETIME_SECS = 600

# How long after this bot joins a room to wait for a human before giving up
# and tearing the session down. The normal case is ~1-3s: `/voice/token`
# doesn't hand iOS a token until this bot has connected, and iOS connects
# immediately after. Anything past that means the client never made it in
# (it crashed, lost the network, or the user backgrounded the app between
# tap and connect) -- without this, such a room stayed billed until
# `_MAX_SESSION_LIFETIME_SECS`, since "on_disconnected" only fires for a
# room that someone actually joined and LiveKit then reaped.
_NOBODY_JOINED_TIMEOUT_SECS = 30

# The LiveKit identity this bot joins rooms under. `/voice/token` mints the
# human's token with `.with_identity(user.id)`, so the two can never
# collide.
_AGENT_PARTICIPANT_IDENTITY = "atlas-voice-agent"

# `_run_dev_session_loop` only: how many consecutive sessions with no human
# in them before the dev loop stops rejoining. Guards the manual-testing
# terminal you walked away from -- a forgotten `python -m app.voice_agent`
# rejoined an empty dev room every `_RECONNECT_DELAY_SECS` forever and billed
# ~12 minutes of continuous reconnects before anyone noticed.
_MAX_DEV_SESSIONS_WITHOUT_A_HUMAN = 5


def _human_participants(participant_ids: Iterable[str]) -> list[str]:
    """The non-agent participants among `participant_ids`.

    `LiveKitTransport.get_participants()` already returns only *remote*
    participants (confirmed against the installed source: it reads
    `room.remote_participants`), so this bot is never in that list to begin
    with; the filter is belt-and-braces for the day a second agent-style
    participant (a recorder, an egress worker) shows up and must not count
    as "someone is still here". In a per-session room there is exactly one
    human, so an empty result means "the human left".
    """
    return [pid for pid in participant_ids if pid != _AGENT_PARTICIPANT_IDENTITY]


async def _end_session_if_nobody_joins(
    end_session: Callable[[str], Awaitable[None]],
    human_joined: asyncio.Event,
    timeout: float = _NOBODY_JOINED_TIMEOUT_SECS,
) -> None:
    """Wait `timeout` seconds for `human_joined`; if it never gets set, end
    the session via `end_session`. Started from the transport's
    "on_connected" handler (so the clock starts when this bot is actually in
    the room, not when `run_voice_session` was called) and cancelled once
    the session ends. See `_NOBODY_JOINED_TIMEOUT_SECS`.
    """
    try:
        await asyncio.wait_for(human_joined.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        await end_session(f"nobody joined within {timeout}s")


async def run_voice_session(
    room_name: str,
    user_id: str,
    timezone: str,
    ready: Optional[asyncio.Event] = None,
) -> bool:
    """Build a fresh token/transport/pipeline and run ONE voice-agent
    session, for the given room/user/timezone, until the human leaves (or
    the room disconnects, or the pipeline itself errors out). Runs exactly
    once -- no auto-rejoin loop -- since a real per-session room (the only
    caller as of the per-session room fix: `app/voice_routes.py`'s
    `/voice/token`) is used by exactly one real conversation and is done
    once that ends; the fixed dev room's own "keep retrying forever" need is
    `_run_dev_session_loop`'s concern, not this function's.

    Returns whether a human participant ever joined this session's room.
    `/voice/token` ignores that (its session task's return value goes
    nowhere); `_run_dev_session_loop` uses it to stop rejoining a dev room
    nobody is showing up in.

    `ready`, if given, is set as soon as this bot is actually connected to
    the room (review finding F1) -- `/voice/token` waits on it before
    returning a token, so the client never connects and starts publishing
    data messages into a room the bot hasn't joined yet (LiveKit doesn't
    queue those for late joiners; they're simply lost). Optional because
    `_run_dev_session_loop` has nobody waiting on it.

    Everything here is rebuilt per call, deliberately -- see the auto-rejoin
    note in the module docstring for why `tools`/`reference_datetime` are
    included in that "everything" rather than hoisted out as a one-time,
    process-lifetime setup step the way they were before.
    """
    tools = build_tools(
        # Found live: datetime.now() is naive, in the SERVER PROCESS's own
        # OS-local timezone -- date_resolution.py/tools.py treat a naive
        # reference datetime as already being wall-clock time IN the target
        # zone (a reasonable convention for /chat, where the client supplies
        # reference_datetime and timezone together, already matched to each
        # other), which is simply wrong whenever the server's OS clock isn't
        # already in that same zone. Building an aware datetime directly in
        # the target zone is correct regardless of the host's own
        # clock/zone.
        reference_datetime=datetime.now(ZoneInfo(timezone)),
        timezone=timezone,
        extractor=get_default_extractor(),
        weather_client=get_default_weather_client(),
        search_client=get_default_web_search_client(),
        user_id=user_id,
        memory_store=get_default_memory_store(),
    )
    function_schemas: list[FunctionSchema] = to_function_schemas(tools)

    token = generate_token_with_agent(
        room_name=room_name,
        participant_name=_AGENT_PARTICIPANT_IDENTITY,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    logger.info(
        "atlas voice agent: joining LiveKit room '%s' at %s for user %s",
        room_name,
        LIVEKIT_URL,
        user_id,
    )

    transport = LiveKitTransport(
        url=LIVEKIT_URL,
        token=token,
        room_name=room_name,
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

    # reference_datetime/tools are now built fresh per call to this function
    # (see the docstring above and the module docstring's auto-rejoin note)
    # rather than once at process startup -- unlike the text /chat path,
    # where every HTTP request already gets its own fresh reference_datetime.
    #
    # get_default_weather_client()/get_default_web_search_client()/
    # get_default_memory_store() diverge from those functions' own
    # "construct fresh, no caching" convention (see app/supabase_client.py)
    # only in that a single session now holds one instance for its own
    # lifetime rather than per-call -- deliberate (their underlying
    # httpx/OpenAI clients are safe for concurrent use), not an oversight.

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
        # This scaffold is meant to sit and wait for a human tester
        # indefinitely -- Pipecat's own default (5 minutes) cancels the
        # whole worker/runner on the first quiet gap between turns, which
        # is exactly what real interactive testing produces (time spent
        # actually talking to a person on the other end, reading a reply,
        # deciding what to ask next). Discovered live: the process silently
        # shut itself down mid-testing-session more than once, each time
        # looking indistinguishable from "Atlas is being slow" until the
        # log was checked directly.
        cancel_on_idle_timeout=False,
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

    # Did a human ever make it into this room? Set by the participant
    # handlers below; doubles as this function's return value and as what
    # the nobody-joined watchdog waits on.
    human_joined = asyncio.Event()
    nobody_joined_watchdog: Optional[asyncio.Task] = None
    # The reason the FIRST exit path to fire gave, or None while the session
    # is still live. There are now four ways out (human left, nobody joined,
    # transport disconnected, lifetime cap) and they routinely cascade --
    # cancelling on the human leaving makes LiveKit close the room, which
    # fires "on_disconnected" against an already-cancelled runner seconds
    # later. `runner.cancel()` is documented as idempotent, so the dedupe is
    # about the log staying honest about which exit actually ended the
    # session, not about correctness.
    ending_reason: Optional[str] = None

    async def _end_session(reason: str) -> None:
        nonlocal ending_reason
        if ending_reason is not None:
            logger.debug(
                "voice agent: room '%s' already ending (%s); ignoring '%s'",
                room_name,
                ending_reason,
                reason,
            )
            return
        ending_reason = reason
        logger.info("voice agent: ending session in room '%s' -- %s", room_name, reason)
        await runner.cancel(reason)

    @transport.event_handler("on_connected")
    async def _on_connected(transport: LiveKitTransport) -> None:
        # The one moment this bot is genuinely IN the room and able to
        # receive data messages -- everything the client publishes before it
        # is dropped by LiveKit, which is the whole of review finding F1.
        # Pipecat passes the transport itself as the handler's first
        # argument (confirmed against the installed source,
        # `utils/base_object.py`'s `_run_handler`), shadowing the outer
        # `transport` name harmlessly; it's the same object.
        nonlocal nobody_joined_watchdog
        logger.info("voice agent: connected to LiveKit room '%s'", room_name)
        # Start the no-show clock here rather than at the top of this
        # function: the LiveKit connect itself (plus building the pipeline)
        # takes a second or two, and it's the time spent waiting in an empty
        # room that costs money.
        nobody_joined_watchdog = asyncio.create_task(
            _end_session_if_nobody_joins(_end_session, human_joined)
        )
        if ready is not None:
            ready.set()

    # Both of these mean "a human is in the room", and both are needed:
    # "on_participant_connected" fires for someone joining after this bot,
    # which is the normal order (`/voice/token` waits for `ready` before
    # handing iOS its token), while "on_first_participant_joined" is what
    # the transport fires for participants who were ALREADY in the room when
    # this bot connected -- confirmed against the installed source's
    # `LiveKitTransportClient.connect()`, which checks `get_participants()`
    # after "on_connected" and fires only the latter for them. Both take
    # `(transport, participant_id)`; setting an already-set Event is a no-op,
    # so the overlap is harmless.
    @transport.event_handler("on_participant_connected")
    async def _on_participant_connected(transport: LiveKitTransport, participant_id: str) -> None:
        if not _human_participants([participant_id]):
            return
        logger.info(
            "voice agent: participant '%s' joined room '%s'", participant_id, room_name
        )
        human_joined.set()

    @transport.event_handler("on_first_participant_joined")
    async def _on_first_participant_joined(
        transport: LiveKitTransport, participant_id: str
    ) -> None:
        if not _human_participants([participant_id]):
            return
        logger.info(
            "voice agent: first participant '%s' already in room '%s'", participant_id, room_name
        )
        human_joined.set()

    @transport.event_handler("on_participant_disconnected")
    async def _on_participant_disconnected(
        transport: LiveKitTransport, participant_id: str
    ) -> None:
        # The real end of a voice turn, and the one that matters for cost:
        # without this the bot sat in the room until LiveKit Cloud's own
        # empty-room reaper got around to closing it (~20-35s observed
        # live), every one of those seconds billed as participant connection
        # time. LiveKit removes the participant from `room.remote_participants`
        # BEFORE emitting this event (confirmed against the installed
        # `livekit/rtc/room.py`: `_remote_participants.pop(identity)` then
        # `emit(...)`), so `get_participants()` here already excludes whoever
        # just left.
        remaining = _human_participants(transport.get_participants())
        if remaining:
            logger.info(
                "voice agent: participant '%s' left room '%s'; %d still there",
                participant_id,
                room_name,
                len(remaining),
            )
            return
        await _end_session("human participant left")

    @transport.event_handler("on_disconnected")
    async def _on_disconnected(transport: LiveKitTransport) -> None:
        # Confirmed live that neither a participant leaving nor LiveKit
        # Cloud's own subsequent room teardown raises or makes
        # WorkerRunner.run() return on its own -- cancelling the runner is
        # what does that, by making run() unblock so this function returns.
        # For the fixed dev room, the caller (the _run_dev_session_loop
        # auto-rejoin loop) reacts by rejoining with a fresh token; for a
        # real per-session room, the caller (voice_routes.py's spawned task)
        # just lets the session end -- its one-shot job is done.
        #
        # This is now usually the SECOND exit to fire rather than the first:
        # `_on_participant_disconnected` above ends the session the moment
        # the human leaves, and LiveKit's room teardown lands here after
        # that. It still matters on its own for the cases the participant
        # events can't see (this bot itself being disconnected, the room
        # being closed server-side).
        logger.warning(
            "voice agent: disconnected from LiveKit room '%s'",
            room_name,
        )
        await _end_session("transport disconnected")

    await runner.add_workers(worker)
    try:
        await asyncio.wait_for(runner.run(), timeout=_MAX_SESSION_LIFETIME_SECS)
    except asyncio.TimeoutError:
        # wait_for has already cancelled the run() coroutine by the time we
        # get here; `runner.cancel(...)` (via _end_session) is the runner's
        # own documented shutdown path and is what actually stops/cleans up
        # the worker's pipeline, so it's not redundant with that
        # cancellation. See _MAX_SESSION_LIFETIME_SECS for why this bound
        # exists at all.
        logger.warning(
            "voice agent: session in room '%s' hit the %ss lifetime cap -- shutting it down",
            room_name,
            _MAX_SESSION_LIFETIME_SECS,
        )
        await _end_session("max session lifetime reached")
    finally:
        # Nothing awaits the watchdog, and in the normal case (a human did
        # join, then left) it's still sitting in its wait_for when the
        # session ends -- left alone it would keep this whole session's
        # closure alive for up to _NOBODY_JOINED_TIMEOUT_SECS and then call
        # back into a dead runner.
        if nobody_joined_watchdog is not None:
            nobody_joined_watchdog.cancel()

    return human_joined.is_set()


async def _run_dev_session_loop() -> None:
    """Auto-rejoin loop against the fixed dev room/account -- ONLY used by
    this module's own `python -m app.voice_agent` CLI entry point for
    manual standalone testing, not by real per-session voice traffic (see
    the module docstring's per-session room note). A session ending (see
    `run_voice_session`'s `_on_disconnected` handler) is the expected,
    common case here (LiveKit Cloud closing an emptied room) and not itself
    an error -- only an actual exception out of `run_voice_session` (e.g. a
    startup failure while building the pipeline) is logged as one. Either
    way, the response is the same: wait briefly, then rejoin with a
    completely fresh token/transport/pipeline/tools.

    "Forever" is now bounded, though: `_MAX_DEV_SESSIONS_WITHOUT_A_HUMAN`
    consecutive sessions that nobody joined and this gives up rather than
    holding a billed LiveKit connection open in a room the tester walked
    away from. Any session a human did join resets the count, so an ordinary
    testing session with gaps between turns is unaffected.
    """
    sessions_without_a_human = 0
    while True:
        try:
            a_human_joined = await run_voice_session(
                room_name=VOICE_DEV_ROOM_NAME, user_id=VOICE_DEV_USER_ID, timezone=VOICE_DEV_TIMEZONE
            )
        except Exception:
            logger.exception("voice agent session ended with an unexpected error")
            # A session that died before anyone could join counts as a
            # no-show -- that bounds a crash-loop (bad credentials, a
            # provider outage) by the same counter instead of retrying
            # every few seconds indefinitely.
            a_human_joined = False

        if a_human_joined:
            sessions_without_a_human = 0
        else:
            sessions_without_a_human += 1
            if sessions_without_a_human >= _MAX_DEV_SESSIONS_WITHOUT_A_HUMAN:
                logger.warning(
                    "voice agent: no one joined the dev room after %s attempts -- exiting; "
                    "rerun when you're ready to test",
                    _MAX_DEV_SESSIONS_WITHOUT_A_HUMAN,
                )
                return

        logger.info("voice agent: reconnecting in %ss", _RECONNECT_DELAY_SECS)
        await asyncio.sleep(_RECONNECT_DELAY_SECS)


async def main() -> None:
    _require_voice_config()
    await _run_dev_session_loop()


if __name__ == "__main__":
    asyncio.run(main())
