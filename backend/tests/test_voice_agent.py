"""Unit tests for the parts of the voice agent's session lifecycle that
don't need a real LiveKit connection -- specifically the exits added by the
LiveKit cost-reduction pass (2026-09-18): who counts as "still in the room",
the nobody-joined watchdog, and the dev loop's give-up counter. The session
wiring itself (transport events, the pipeline) is only exercisable against a
live room, so the logic those handlers depend on is deliberately kept in
small pure/injectable helpers that can be tested here.
"""

import asyncio

from app import voice_agent


def test_human_participants_excludes_the_agent_itself() -> None:
    assert voice_agent._human_participants([voice_agent._AGENT_PARTICIPANT_IDENTITY]) == []


def test_human_participants_keeps_real_participants() -> None:
    # A real human's LiveKit identity is their Supabase user id (see
    # /voice/token's `.with_identity(user.id)`).
    participants = ["325c07ec-8e45-49a1-931e-d29a40ddffce", voice_agent._AGENT_PARTICIPANT_IDENTITY]
    assert voice_agent._human_participants(participants) == [
        "325c07ec-8e45-49a1-931e-d29a40ddffce"
    ]


def test_human_participants_of_an_empty_room_is_empty() -> None:
    # The decision `_on_participant_disconnected` makes: nothing left to
    # talk to, so end the session rather than wait for LiveKit's reaper.
    assert voice_agent._human_participants([]) == []


def test_nobody_joined_watchdog_ends_the_session() -> None:
    ended: list[str] = []

    async def _end_session(reason: str) -> None:
        ended.append(reason)

    async def _run() -> None:
        await voice_agent._end_session_if_nobody_joins(
            _end_session, asyncio.Event(), timeout=0.01
        )

    asyncio.run(_run())
    assert len(ended) == 1
    assert "nobody joined" in ended[0]


def test_nobody_joined_watchdog_stays_quiet_once_someone_joins() -> None:
    ended: list[str] = []

    async def _end_session(reason: str) -> None:
        ended.append(reason)

    async def _run() -> None:
        human_joined = asyncio.Event()
        watchdog = asyncio.create_task(
            voice_agent._end_session_if_nobody_joins(_end_session, human_joined, timeout=5)
        )
        # The normal case: iOS connects ~1-3s after being handed its token.
        await asyncio.sleep(0)
        human_joined.set()
        await watchdog

    asyncio.run(_run())
    assert ended == []


def test_session_lifetime_cap_is_minutes_not_an_hour() -> None:
    # Regression guard for the cost fix: this was 3600 (the token TTL), so
    # an orphaned one-turn session billed LiveKit for a full hour.
    assert voice_agent._MAX_SESSION_LIFETIME_SECS == 600


def _patch_dev_loop(monkeypatch, joins: list[bool]) -> list[str]:
    """Make `_run_dev_session_loop` run instantly, with `run_voice_session`
    reporting `joins[i]` ("did a human join?") for the i-th session. Returns
    the list each call appends its room name to, i.e. the session count.
    """
    calls: list[str] = []
    remaining = list(joins)

    async def _fake_run_voice_session(room_name: str, user_id: str, timezone: str) -> bool:
        calls.append(room_name)
        # Keep going rather than IndexError if the loop overshoots -- the
        # assertion on `calls` gives a far more readable failure.
        return remaining.pop(0) if remaining else False

    monkeypatch.setattr(voice_agent, "run_voice_session", _fake_run_voice_session)
    monkeypatch.setattr(voice_agent, "_RECONNECT_DELAY_SECS", 0)
    return calls


def test_dev_loop_gives_up_after_repeated_no_shows(monkeypatch) -> None:
    calls = _patch_dev_loop(monkeypatch, [False] * 10)

    asyncio.run(voice_agent._run_dev_session_loop())

    assert len(calls) == voice_agent._MAX_DEV_SESSIONS_WITHOUT_A_HUMAN


def test_dev_loop_counter_resets_when_someone_actually_joins(monkeypatch) -> None:
    # Two no-shows, then a real testing session, then five more no-shows:
    # the loop must not give up until the run of five AFTER the real one.
    calls = _patch_dev_loop(monkeypatch, [False, False, True] + [False] * 10)

    asyncio.run(voice_agent._run_dev_session_loop())

    assert len(calls) == 3 + voice_agent._MAX_DEV_SESSIONS_WITHOUT_A_HUMAN


def test_dev_loop_gives_up_on_a_crash_loop(monkeypatch) -> None:
    # A session that raises never had a human in it, so it counts as a
    # no-show -- otherwise a startup failure (bad credentials, provider
    # outage) would retry every few seconds forever.
    calls: list[str] = []

    async def _failing_run_voice_session(room_name: str, user_id: str, timezone: str) -> bool:
        calls.append(room_name)
        raise RuntimeError("provider is down")

    monkeypatch.setattr(voice_agent, "run_voice_session", _failing_run_voice_session)
    monkeypatch.setattr(voice_agent, "_RECONNECT_DELAY_SECS", 0)

    asyncio.run(voice_agent._run_dev_session_loop())

    assert len(calls) == voice_agent._MAX_DEV_SESSIONS_WITHOUT_A_HUMAN
