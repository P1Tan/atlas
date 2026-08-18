# Atlas

A personal assistant, starting with a text-first email-to-calendar tool. See [`assistant-spec.md`](assistant-spec.md) for the full product/architecture spec and [`CLAUDE_HANDOFF.md`](CLAUDE_HANDOFF.md) for the build plan. Live increment status is in [`PROGRESS.md`](PROGRESS.md).

## Status

Early scaffolding — see `PROGRESS.md` for what's built so far.

## Repo layout

```
backend/   FastAPI service (LLM extraction, holds all API keys)
ios/       SwiftUI app (iOS 26+)
```

## Backend

Setup and run instructions land in Increment 0.2 (`PROGRESS.md`).

## iOS

Setup and run instructions land in Increment 0.3 (`PROGRESS.md`). Bundle ID: `com.p1tan.atlas`, minimum iOS 26.

## Environment variables

Copy `.env.example` to `.env` and fill in real values. `.env` is gitignored and must never be committed.
