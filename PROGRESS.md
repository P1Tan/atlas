# Progress — Atlas

Living checklist. Check off items as increments complete; add a one-line note on anything learned. See `CLAUDE_HANDOFF.md` §7 for full increment definitions and `CLAUDE.md` for standing conventions.

## Milestone 0 — Repo & skeletons
- [x] 0.1 Repo bootstrap — git init, `.gitignore`, `README.md`, `.env.example`, `CLAUDE.md`, `PROGRESS.md`, GitHub remote connected (`https://github.com/P1Tan/atlas.git`), bundle ID `com.p1tan.atlas` confirmed.
- [ ] 0.2 Backend skeleton — FastAPI app, `GET /health`, one passing test, README run instructions.
- [ ] 0.3 iOS skeleton — SwiftUI app builds, single screen, calls `/health`, displays status.

## Milestone 1 — Extraction pipeline (backend)
- [ ] 1.1 Event schema + stub — Pydantic Event model, `POST /extract` returns hardcoded example events, shape tests.
- [ ] 1.2 Real LLM extraction — GPT-5 mini via tool calling behind a swappable model interface; log model + (later) correction; unit tests over sample emails.
- [ ] 1.3 Date resolution — deterministic resolution of `date_phrase` to `resolved_start`/`resolved_end`; tests across weekdays, "next week," month-end, "no date."
- [ ] 1.4 Ambiguity flagging — `confidence` and `ambiguities` populated; never guess an uninferable year/time; tests.

## Milestone 2 — iOS input, review, write
- [ ] 2.1 Paste input — text box screen, submits to `/extract`, lists proposed events (read-only).
- [ ] 2.2 Editable review UI — every field editable, ambiguous fields visually flagged.
- [ ] 2.3 EventKit write — calendar permission request, confirm-before-write enforced, verified in iOS Calendar app.
- [ ] 2.4 Share-sheet intake — share extension feeds forwarded email into the same `/extract` flow.

## Milestone 3 — Live Gmail (Phase 0 v1)
- [ ] 3.1 Google OAuth (read-only) — server-side OAuth, restricted Gmail scope, testing mode, tokens server-side only.
- [ ] 3.2 Fetch & extract — pull recent/unread messages, run through `/extract`, present for review.
- [ ] 3.3 Privacy guardrails — recent/unread-only enforced, in-app consent copy, excerpt-only retention.

## Milestone V — Voice-loop spike (optional, isolated, throwaway)
- [ ] V.1 `spike/voice/` record → STT → LLM → TTS → play loop, time-to-first-audio measured on a real device against the §13 latency budget. Not wired into the app.

---

## Notes log

- 2026-08-18 — Kickoff. GitHub remote confirmed as `https://github.com/P1Tan/atlas.git` (private, created manually since `gh` CLI is not installed on this machine). Bundle ID handle set to `p1tan` (from GitHub username `P1Tan`) → `com.p1tan.atlas`.
