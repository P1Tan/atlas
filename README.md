# Atlas

A personal assistant, starting with a text-first email-to-calendar tool. See [`assistant-spec.md`](assistant-spec.md) for the full product/architecture spec.

## Status

Phase 0 (text-first email-to-calendar) is complete: `/extract` does LLM-based event extraction with deterministic date resolution and ambiguity flagging; live Gmail (read-only OAuth) feeds the same pipeline; iOS lets you paste, share from Mail, or pull from Gmail, review/edit proposed events, and confirm to write them to the calendar via EventKit.

Phase 1 (MVP) is underway. `/chat` is a general multi-turn, tool-calling conversation endpoint with a configurable persona (`ATLAS_PERSONA` env var, see `.env.example`) and three real tools: `extract_calendar_events` (proposes events from pasted text, same pipeline `/extract` uses), `set_reminder` (schedules a real on-device local notification via iOS's `ReminderScheduler`, given a title and natural-language time), and `get_weather` (current conditions + short forecast via the free, keyless Open-Meteo API). Milestone 4 (chat core & persona) is complete; Milestone 5 (remaining MVP tools) is underway.

## Repo layout

```
backend/   FastAPI service (LLM extraction, holds all API keys)
ios/       SwiftUI app (iOS 26+)
```

## Backend

Requires Python 3.9+.

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

## Environment variables

Copy `.env.example` to `.env` and fill in real values. `.env` is gitignored and must never be committed.
