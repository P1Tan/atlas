# Atlas

A personal assistant, starting with a text-first email-to-calendar tool. See [`assistant-spec.md`](assistant-spec.md) for the full product/architecture spec.

## Status

Early scaffolding: backend health check is up. iOS app not started yet.

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

Not started yet. Bundle ID: `com.p1tan.atlas`, minimum iOS 26.

## Environment variables

Copy `.env.example` to `.env` and fill in real values. `.env` is gitignored and must never be committed.
