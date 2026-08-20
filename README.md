# Atlas

A personal assistant, starting with a text-first email-to-calendar tool. See [`assistant-spec.md`](assistant-spec.md) for the full product/architecture spec.

## Status

Backend: health check + email-to-calendar extraction (`/extract`) with LLM-based event extraction, deterministic date resolution, and ambiguity flagging. iOS: paste-input screen calling `/extract` and listing proposed events read-only; editable review and calendar writes are next.

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

`AtlasUITests` drives the real app in the simulator via XCUITest (no mocking). The extraction-flow tests need the backend running locally first:

```
cd ios
xcodebuild test -project Atlas.xcodeproj -scheme Atlas -destination 'platform=iOS Simulator,name=iPhone 17'
```

## Environment variables

Copy `.env.example` to `.env` and fill in real values. `.env` is gitignored and must never be committed.
