# CLAUDE.md — Atlas persistent conventions

This file is the persistent memory for building Atlas across sessions. Read it at the start of every session, alongside `assistant-spec.md` (product/architecture source of truth) and `CLAUDE_HANDOFF.md` (build-order playbook). If anything here conflicts with those, `CLAUDE_HANDOFF.md` governs *how/when* to build, `assistant-spec.md` governs *what*.

---

## Current phase

**Phase 0, text-first email-to-calendar.** Not building voice, memory, persona, or the signature wedge yet. See `CLAUDE_HANDOFF.md` §7 for the increment plan and `PROGRESS.md` for live status.

---

## Committed tech stack (do not substitute without asking)

- **iOS client:** native SwiftUI, iOS only for v1, **minimum iOS 26**. Bundle ID: `com.p1tan.atlas`.
- **Calendar writes:** native EventKit (on-device, no extra OAuth).
- **Backend:** Python + FastAPI. Holds all API keys; runs LLM extraction.
- **LLM:** starts with **GPT-5 mini**, called via tool calling, behind one swappable model interface. Never call the model from the iOS app directly — always through the backend.
- **Email (Milestone 3):** Gmail API, read-only scope, OAuth testing mode, user as sole test user.
- **Voice (later, not now):** Pipecat on a LiveKit transport. Out of scope for Phase 0 feature work; only a throwaway, isolated spike (`spike/voice/`) is allowed, per `CLAUDE_HANDOFF.md` Milestone V.
- **Data/auth:** Supabase or Firebase, only once actually needed.

---

## Scope guardrails

- The "why open this over Siri" signature wedge (`assistant-spec.md` §4) is **deliberately unchosen**. Do not build or assume it.
- Phase 0 is text-first. Do not build the voice pipeline as part of feature work.
- Build **one increment at a time** (`CLAUDE_HANDOFF.md` §7). If something needs a decision beyond the current increment — a new dependency, an architectural change, anything ambiguous in the spec — **stop and ask the user**, don't guess.
- When in doubt, do less, and ask.

---

## Invariants (never violate, across all increments)

- **Confirm-before-write.** Never create a calendar event without explicit user confirmation in the review UI. No silent/auto creation, ever.
- **Keys server-side only.** The iOS app never holds an LLM/Gmail API key. All model and email-provider calls go through the backend.
- **Date math in code, not the model.** The LLM extracts the temporal phrase as text; deterministic backend code resolves it to an absolute date against a reference timestamp + timezone.
- **Email privacy.** Never log full email bodies. Process only mail the user selected or recent/unread — never sweep the whole inbox by default. Keep only a short, excerpt-only `source_excerpt`.
- **Ambiguity is surfaced, not guessed.** Low-confidence fields are flagged for the user rather than silently filled.

---

## Git workflow

- **Branch per increment**, PR into `main`, self-merge once green (`gh pr create`). Solo project — trunk-based direct-to-main is an acceptable fallback only if the user asks for it explicitly.
- **Conventional Commits**: `feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:`. One logical change per commit.
- **Green before push.** Must build and pass tests/checks before committing. Never push broken code or comment out failing tests to force green.
- **Secrets never committed.** Real keys only in local `.env` (gitignored). `.env.example` holds variable names only.
- **Update `PROGRESS.md` every increment** (check off the item, note anything learned), in the same commit as the code.
- **Each push leaves a runnable app/backend** — a reviewer cloning `main` at any commit can follow `README.md` and run what exists so far.
- **End every increment with a short summary**: what changed, how it was verified, the commit/PR link, and the next increment.

---

## Model orchestration (architect / manager pattern)

- **Architect/manager = highest available tier model.** Owns spec interpretation, increment planning, architectural/design decisions, tricky logic (date resolution, anything touching the invariants above), review of all subagent output, and the git workflow + Definition of Done.
- **Delegate mechanical/bounded work** (boilerplate, scaffolding, routine edits, tests-to-spec, repetitive refactors, simple lookups) to appropriately-tiered subagents, each with a tight, self-contained brief.
- **Orchestrator reviews everything before commit.** Never push subagent output blindly. Invariants and secret-handling apply exactly as to any other code.
- **Don't over-engineer the hierarchy** — trivial increments can just be done directly.
- **Do not conflate two distinct "models."** The tiered models here build Atlas (Claude Code's own subagents). Atlas's own runtime brain is a separate thing — starts as GPT-5 mini (see stack above) — the model the *shipped app* calls to extract events. Changing a build-time tier never changes the app's runtime model, and vice versa.

---

## Definition of done (per increment)

- [ ] Implements exactly the current increment, nothing more
- [ ] Builds cleanly; tests/checks pass
- [ ] Manually verified (state how)
- [ ] Invariants upheld
- [ ] No secrets committed
- [ ] `PROGRESS.md` updated
- [ ] Conventional-commit message; committed and pushed
- [ ] Short summary reported to the user, with next increment named

Stop after each milestone (Milestone 0, 1, 2, 3, V in `CLAUDE_HANDOFF.md` §7) and confirm with the user before starting the next.
