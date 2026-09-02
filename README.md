# Atlas

A personal assistant, starting with a text-first email-to-calendar tool. See [`assistant-spec.md`](assistant-spec.md) for the full product/architecture spec.

## Status

Phase 0 (text-first email-to-calendar) is complete: `/extract` does LLM-based event extraction with deterministic date resolution and ambiguity flagging; live Gmail (read-only OAuth) feeds the same pipeline; iOS lets you paste, share from Mail, or pull from Gmail, review/edit proposed events, and confirm to write them to the calendar via EventKit.

Phase 1 (MVP) is underway. `/chat` is a general multi-turn, tool-calling conversation endpoint with a configurable persona (`ATLAS_PERSONA` env var, see `.env.example`) and four real tools: `extract_calendar_events` (proposes events from pasted text, same pipeline `/extract` uses), `set_reminder` (schedules a real on-device local notification via iOS's `ReminderScheduler`, given a title and natural-language time), `get_weather` (current conditions + short forecast via the free, keyless Open-Meteo API), and `web_search` (fresh information via the Tavily API — needs `TAVILY_API_KEY`, see `.env.example`). Milestone 4 (chat core & persona) and Milestone 5 (remaining MVP tools) are complete.

The app now requires sign-in (Supabase Auth, magic-link email) — `/chat` requires a valid bearer token, and iOS gates the whole app behind a sign-in screen until one exists. See `.env.example` for the `SUPABASE_*` variables needed to run the backend.

## Repo layout

```
backend/   FastAPI service (LLM extraction, holds all API keys)
ios/       SwiftUI app (iOS 26+)
```

## Backend

Requires Python 3.11+ (raised from 3.9 in Milestone 7.1 — `pipecat-ai`'s actively-maintained voice-pipeline API requires it).

```
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# run the dev server
uvicorn app.main:app --reload

# run tests
pytest
```

`GET /health` returns `{"status": "ok"}` once the server is running.

## iOS

Requires Xcode 26+. The project is generated with [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`) from `ios/project.yml`; `Atlas.xcodeproj` is committed, so XcodeGen is only needed if you change `project.yml` and want to regenerate it (`cd ios && xcodegen generate`).

```
open ios/Atlas.xcodeproj
```

Build and run on a simulator (Cmd-R). The app talks to `http://127.0.0.1:8000` — start the backend first (see above) so it has something to reach. Bundle ID: `com.p1tan.atlas`, minimum iOS 26.

### UI tests

`AtlasUITests` drives the real app in the simulator via XCUITest (no mocking). The extraction-flow tests need the backend running locally first, and the calendar-write test needs calendar access pre-granted (otherwise it'll hit a permission prompt XCUITest doesn't handle):

```
xcrun simctl privacy <device-udid> grant calendar com.p1tan.atlas

cd ios
xcodebuild test -project Atlas.xcodeproj -scheme Atlas -destination 'platform=iOS Simulator,name=iPhone 17'
```

## Voice agent (Milestone 7.1 scaffold)

A standalone script (`backend/app/voice_agent.py`), not part of the FastAPI server, that proves the Pipecat + LiveKit voice pipeline works end-to-end (speech-to-text → the same tool-calling LLM chat loop and persona as `/chat` → text-to-speech) and gives a rough latency read. It is a scaffold: not production voice UX, and there is no LiveKit agent auto-dispatch in Pipecat, so this script owns joining the room itself, like any other participant.

Requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` in `.env` (a free [LiveKit Cloud](https://cloud.livekit.io) project) — see `.env.example`. Reuses the existing `OPENAI_API_KEY` for STT/LLM/TTS, so no other new keys are needed.

```
cd backend
source .venv/bin/activate
python -m app.voice_agent
```

This joins a fixed dev room (`ATLAS_VOICE_DEV_ROOM_NAME`, default `atlas-dev`) as an agent participant and waits there. To actually talk to it, join the *same* room from a browser at [meet.livekit.io](https://meet.livekit.io) using the same `LIVEKIT_URL` and a token minted for a human participant. Generating that human token is a manual step for now — either use the LiveKit Cloud dashboard's "Generate token" feature for that room, or a one-off Python snippet:

```python
from datetime import timedelta
from livekit import api
token = api.AccessToken("<LIVEKIT_API_KEY>", "<LIVEKIT_API_SECRET>")
token.with_identity("dev-tester").with_grants(api.VideoGrants(room_join=True, room="atlas-dev"))
token.with_ttl(timedelta(hours=1))  # a leaked token grants room access without the secret itself
print(token.to_jwt())
```

## Environment variables

Copy `.env.example` to `.env` and fill in real values. `.env` is gitignored and must never be committed.
