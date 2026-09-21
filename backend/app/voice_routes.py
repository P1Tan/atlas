"""Milestone 7.2b -- mints a LiveKit join token for a real human participant
(the iOS app) and spawns a dedicated per-session bot to join it.

Deliberately does NOT use `pipecat.runner.livekit.generate_token_with_agent`
(see app/voice_agent.py) -- that helper sets `agent=True` in the video grant,
a marker meant for the bot participant, not a human. This calls
`livekit.api.AccessToken` directly with a plain `room_join` grant instead,
mirroring that helper's own token-building shape (confirmed against its
installed source) minus the `agent=True` grant and `with_name`.

As of the per-session room fix (bug audit finding, HIGH -- cross-user
privacy leak): every authenticated user used to get a token for the SAME
fixed `VOICE_DEV_ROOM_NAME` that `app/voice_agent.py`'s bot also joined --
any two real accounts would land in the identical LiveKit room, able to
hear each other's live conversation and (since any room participant can
publish LiveKit data messages) potentially inject spoofed
`assistant_reply`/`tool_result` messages into someone else's session. Each
call here now mints a fresh, unique room and spawns a dedicated
`app.voice_agent.run_voice_session` task in this same process's event loop
to join it -- true per-session isolation. `python -m app.voice_agent` as a
separately-run process is no longer needed for real app usage; it still
exists only for manual standalone testing (see that module's docstring).
"""

import asyncio
import logging
import uuid
from datetime import timedelta
from functools import partial
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from livekit import api
from pydantic import BaseModel

from app.config import (
    CARTESIA_API_KEY,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    VOICE_DEV_TIMEZONE,
)
from app.rate_limit import enforce_voice_token_rate_limit
from app.supabase_client import AuthenticatedUser, get_current_user
from app.voice_agent import run_voice_session

logger = logging.getLogger("atlas.voice")

router = APIRouter(prefix="/voice", tags=["voice"])

# Holds a strong reference to every in-flight per-session bot task -- a real
# asyncio gotcha, not belt-and-suspenders: a Task with no surviving
# reference can be garbage-collected before it completes, silently killing
# a real user's live voice session. Each task removes itself once it's
# genuinely done (successfully, or via an error `run_voice_session` itself
# didn't catch), so this doesn't grow unbounded across many sessions.
#
# Keyed by user id (review finding F2 -- was a plain `set` of tasks): a user
# is only ever in ONE voice session at a time, but iOS's continuous-
# conversation mode requests a NEW token right after every reply, and the
# previous bot keeps its full Pipecat pipeline (OpenAI + Cartesia websockets)
# alive in its now-abandoned room until LiveKit's empty-room reaper gets
# around to it (~20-35s observed live) -- so every turn used to overlap two
# live pipelines, and a retry loop could stack them up to the rate limit
# (30/min). Keying by user makes the previous task findable so it can be
# cancelled explicitly here, which is a clean shutdown (`WorkerRunner.run`
# handles CancelledError and tears the pipeline down).
_active_voice_sessions: dict[str, asyncio.Task] = {}

# How long `/token` waits for the spawned bot to actually be connected to
# the room before giving up (review finding F1). Sized against the observed
# ~1-3s join time (build_tools + LiveKitTransport connect) with headroom for
# a cold provider init, and kept well under iOS's own ~15s heartbeat
# deadline so a slow bot surfaces here as a clean 503 rather than there as
# "Lost connection to Atlas" mid-session.
_BOT_READY_TIMEOUT_SECS = 10.0


class VoiceTokenResponse(BaseModel):
    url: str
    room_name: str
    token: str


def _log_session_outcome(user_id: str, task: asyncio.Task) -> None:
    # Only drop the entry if it's still THIS task: by the time a cancelled
    # task's callback runs, the newer token request that cancelled it has
    # already stored its own task under the same key, and discarding blindly
    # would orphan that live session (losing the strong reference this dict
    # exists to hold, and hiding it from the next request's cancel step).
    if _active_voice_sessions.get(user_id) is task:
        del _active_voice_sessions[user_id]
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("voice session task ended with an unexpected error", exc_info=exc)


@router.post("/token", response_model=VoiceTokenResponse)
async def create_voice_token(
    timezone: Optional[str] = Query(
        default=None,
        description="IANA timezone identifier for this voice session, e.g. America/Los_Angeles.",
    ),
    user: AuthenticatedUser = Depends(get_current_user),
    _rate_limit: None = Depends(enforce_voice_token_rate_limit),
) -> VoiceTokenResponse:
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET and CARTESIA_API_KEY):
        raise HTTPException(status_code=503, detail="voice pipeline not configured")

    # Review finding F5: every real user's voice session used to run with
    # VOICE_DEV_TIMEZONE hardcoded, so date resolution and set_reminder's
    # "has this already passed?" check were wrong for anyone outside that
    # one zone -- the same bug class already fixed for `datetime.now()` in
    # app/voice_agent.py. The client now sends its own
    # `TimeZone.current.identifier` (as /chat already does); the parameter
    # stays optional and falls back to VOICE_DEV_TIMEZONE so an older iOS
    # build that doesn't send it keeps working instead of 422-ing.
    session_timezone = timezone or VOICE_DEV_TIMEZONE
    try:
        # Validated here rather than deep inside the spawned task: an
        # unknown zone would otherwise blow up `ZoneInfo(timezone)` in
        # run_voice_session, after this route had already handed out a
        # perfectly valid token for a room whose bot promptly died.
        # ZoneInfo raises ValueError (not ZoneInfoNotFoundError) for keys
        # that aren't valid zone paths at all, e.g. "../etc/passwd".
        ZoneInfo(session_timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"unknown timezone: {session_timezone}"
        ) from exc

    # A real, unique room per session -- not a shared/predictable name -- is
    # the actual fix here; see the module docstring.
    room_name = f"atlas-voice-{uuid.uuid4().hex}"

    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(user.id)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_ttl(timedelta(hours=1))
        .to_jwt()
    )

    # F2: tear down this user's previous session before starting another.
    # Not merely tidy -- without it, continuous-conversation mode leaves a
    # fully-live pipeline running in the abandoned room after every single
    # turn (see the _active_voice_sessions comment above). Cancelling is the
    # supported shutdown path, and it's deliberately not awaited: the old
    # bot is in a DIFFERENT room, so its teardown can't interfere with the
    # new session, and making the user wait for it would add latency to
    # exactly the hot path this fix is about.
    previous = _active_voice_sessions.get(user.id)
    if previous is not None and not previous.done():
        logger.info("cancelling previous voice session task for user %s", user.id)
        previous.cancel()

    # F1: the bot joining the room is now a precondition of this response,
    # not a race. iOS calls `room.connect()` and immediately publishes
    # `context_seed` + `location`, then starts its heartbeat -- and LiveKit
    # does NOT queue data messages for participants who join later, so
    # anything published before the bot arrived was silently dropped
    # (conversation continuity and location context gone, intermittently,
    # depending on how the ~1-3s bot join lined up with the client). The bot
    # sets `ready` from its transport's own "on_connected" event; this route
    # doesn't hand out the token until that's happened.
    #
    # Still runs in-process on this same FastAPI/uvicorn event loop (this
    # route is `async def` specifically so `asyncio.create_task` attaches to
    # the right loop -- a sync route handler runs in a threadpool thread,
    # which has none).
    ready = asyncio.Event()
    task = asyncio.create_task(
        run_voice_session(
            room_name=room_name,
            user_id=user.id,
            timezone=session_timezone,
            ready=ready,
        )
    )
    _active_voice_sessions[user.id] = task
    task.add_done_callback(partial(_log_session_outcome, user.id))

    try:
        await asyncio.wait_for(ready.wait(), timeout=_BOT_READY_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        # Handing out a token to a room with no bot in it is strictly worse
        # than failing here: the client would connect, talk to nobody, and
        # only find out ~15s later via its own heartbeat timeout. A session
        # task that died early (bad credentials, provider outage) never sets
        # the event either, so it lands here too -- just after the full
        # timeout -- and its actual exception is logged by
        # _log_session_outcome.
        task.cancel()
        logger.error(
            "voice bot did not join room '%s' within %ss -- failing the token request",
            room_name,
            _BOT_READY_TIMEOUT_SECS,
        )
        raise HTTPException(status_code=503, detail="voice pipeline unavailable")

    return VoiceTokenResponse(url=LIVEKIT_URL, room_name=room_name, token=token)
