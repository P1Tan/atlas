# Handoff — Atlas (Personal Assistant, iOS) — Build Kickoff for Claude Code

**Purpose.** This document briefs you (Claude Code) to begin building the app incrementally, with a commit and push to GitHub after every small, verified step. Read it fully before writing any code. The companion spec (`assistant-spec.md`) is the authoritative product/architecture reference; this document governs *how* to build and *in what order*.

---

## 0. How to use this document

- Treat `assistant-spec.md` as the **source of truth** for scope and decisions. This file governs build order and workflow.
- Work **one increment at a time** (see §7). Each increment is small, ends in a working state, and is committed + pushed before starting the next.
- **Do not scope-creep.** Build only the current increment. If something seems to need a decision beyond the current increment (new dependency, architectural change, anything ambiguous in the spec), **stop and ask the user** rather than guessing.
- Your very first actions are in §8 (repo setup + create `CLAUDE.md` and `PROGRESS.md`). Do those before any feature code.
- Adopt the **architect/manager model-orchestration pattern in §11** from the start — run the orchestrating role on the highest available tier model and delegate smaller tasks to appropriately-tiered subagents. Fold a short statement of this into `CLAUDE.md` so it persists across sessions.

---

## 1. What we're building (one paragraph)

**Atlas** — a personal assistant iOS app. Long-term vision: talk or type to it, and it takes real actions via tools, with memory that personalizes it. **We are not building all of that now.** The immediate goal (Phase 0) is the **first feature only**: an **email-to-calendar** tool built **text-first** — paste or share an email, the backend extracts the event(s), the app shows them for review, and on confirm writes them to the device calendar. Voice comes later; it is only ever a front end to the same text pipeline. See `assistant-spec.md` §8.1 for the feature and §16 for phasing.

---

## 2. Scope guardrails (read before every session)

- **Signature wedge is undecided.** `assistant-spec.md` §4 (the "why open this over Siri" capability) is deliberately unchosen. **Do not build or assume it.** Email-to-calendar is a useful tool, *not* that wedge.
- **Phase 0 is text-first.** Do **not** build the voice pipeline (Pipecat/LiveKit/STT/TTS) as part of the feature work. A throwaway voice *spike* is optional and isolated (§7, Milestone V) — it must never block or entangle the main app.
- **Invariants that must never be violated** (see §6): confirm-before-write, secrets server-side only, never log full email bodies, process only selected/recent mail.
- When in doubt, do less, and ask.

---

## 3. Committed tech stack (from the spec — do not substitute without asking)

- **iOS client:** native **SwiftUI** (iOS only for v1).
- **Calendar writes:** native **EventKit** (on-device; no extra OAuth).
- **Backend:** **Python + FastAPI** (holds all API keys; runs the LLM extraction).
- **LLM:** **start with GPT-5 mini**, called via tool calling, behind **one swappable model interface** (do not scatter provider-specific calls through the codebase). The choice is deliberately reversible: escalate to a stronger model (e.g. Claude Haiku 4.5) only if extraction quality demands it — see §9 and the logging note in Increment 1.2. Never call the model from the iOS app directly; all calls go through the backend.
- **Email (later in Phase 0):** Gmail API, **read-only** scope, OAuth in testing mode with the user as sole test user.
- **Voice (LATER, not now):** Pipecat on a LiveKit transport. Out of scope for Phase 0 feature work.
- **Data/auth (when needed):** Supabase or Firebase. Not required for the earliest increments.

---

## 4. Target repo structure

```
/                      repo root
├── CLAUDE.md          persistent conventions + invariants (you create this first)
├── PROGRESS.md        living checklist you update every increment
├── CLAUDE_HANDOFF.md  this file
├── assistant-spec.md  product/architecture source of truth
├── README.md          how to run backend + app
├── .gitignore
├── .env.example       names of required env vars, no values
├── backend/           FastAPI service
│   ├── app/
│   └── tests/
│   └── pyproject.toml (or requirements.txt)
└── ios/               SwiftUI app (Xcode project)
```

---

## 5. Git workflow & push cadence (the core requirement)

**Cadence: commit and push after every increment that builds and passes its checks.** Never leave `main` in a broken state. Increments are intentionally small (roughly an hour or less of work) so pushes are frequent and each push is a working checkpoint.

Rules:
1. **Branch per increment.** Create a short-lived branch named like `feat/0.2-backend-health`. Do the increment, then open a PR into `main` (use `gh pr create`). For a solo project, self-merge once green. (If the user prefers trunk-based, committing directly to `main` per green increment is acceptable — confirm their preference in §9.)
2. **Conventional Commits.** Use `feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:` prefixes. One logical change per commit. Example: `feat(backend): add /extract endpoint returning stubbed events`.
3. **Green before push.** The project must build and all tests must pass before you commit. If a step can't be made green, stop and report — do not push broken code or comment out failing tests to force green.
4. **Secrets never committed.** Real keys live only in a local `.env` (gitignored). Commit `.env.example` with variable *names* only. If you ever detect a secret staged, stop and remove it before committing.
5. **Update `PROGRESS.md` every increment** (check off the item, note anything learned) and include it in the same commit.
6. **Each push leaves a runnable app/backend.** A reviewer cloning `main` at any commit should be able to follow `README.md` and run what exists so far.
7. **End every increment with a short summary** to the user: what changed, how it was verified, the commit/PR link, and what the next increment is.

---

## 6. Invariants (guardrails that hold across all increments)

- **Confirm-before-write.** The app must never create a calendar event without explicit user confirmation in the review UI. No silent/auto creation, ever. A misread email writing a wrong event is the worst failure mode.
- **Keys server-side only.** The iOS app never holds an LLM/Gmail API key. All model and email-provider calls go through the backend.
- **Date math in code, not the model.** The LLM extracts the temporal *phrase* as text; deterministic backend code resolves it to an absolute date against a reference timestamp + timezone. (Same pattern proven in the user's earlier braindump project.)
- **Email privacy.** Never log full email bodies. Process only mail the user selected or recent/unread — never sweep the whole inbox by default. Keep a short, excerpt-only `source_excerpt` for user verification, not the full message.
- **Ambiguity is surfaced, not guessed.** Low-confidence fields are flagged for the user rather than silently filled.

---

## 7. Incremental build plan

Each increment below is a unit of work: implement → verify → update `PROGRESS.md` → commit → push. Definition of Done (DoD) for every increment: **builds, checks/tests pass, manually verified, `PROGRESS.md` updated, committed and pushed, summary reported.**

### Milestone 0 — Repo & skeletons
- **0.1 Repo bootstrap.** Init git, `.gitignore`, `README.md`, `.env.example`, create `CLAUDE.md` and `PROGRESS.md` (§8). Connect the GitHub remote (user provides URL or authorize `gh`). Push.
- **0.2 Backend skeleton.** FastAPI app with `GET /health` returning ok; one passing test; run instructions in README. Push.
- **0.3 iOS skeleton.** SwiftUI app that builds, shows a single screen, calls `/health`, and displays the status. Push.

### Milestone 1 — Extraction pipeline (backend)
- **1.1 Event schema + stub.** Define the Event model (§10) as Pydantic types and a `POST /extract` endpoint that accepts raw text and returns a hardcoded example `Event[]`. Tests for request/response shape. Push.
- **1.2 Real LLM extraction.** Behind `/extract`, call the LLM (**GPT-5 mini** to start) via tool calling to produce `Event[]` from the text, with the temporal expression captured as a text phrase (not a resolved date). Put the call behind a **swappable model interface** so the provider/model is a one-line change. **Log which model produced each extraction and (later, once the review UI exists) whether the user corrected it**, so model quality can be judged on this task with real data rather than guesswork. Unit tests over a few sample emails. Push.
- **1.3 Date resolution.** Deterministic code resolves the temporal phrase to `resolved_start`/`resolved_end` against a passed-in reference datetime + timezone. Tests across weekdays, "next week," month-end edges, and "no date." Push.
- **1.4 Ambiguity flagging.** Populate `confidence` and `ambiguities`; never guess a year/time that isn't inferable. Tests. Push.

### Milestone 2 — iOS input, review, write
- **2.1 Paste input.** A screen with a text box; on submit, calls `/extract` and lists the proposed events (read-only). Push.
- **2.2 Editable review UI.** Every field of a proposed event is editable; ambiguous fields are visually flagged. Push.
- **2.3 EventKit write.** Request calendar permission, and on explicit confirm write the event(s) via EventKit. Enforce confirm-before-write. Verify events appear in the iOS Calendar app. Push.
- **2.4 Share-sheet intake.** A share extension so an email can be forwarded/shared from Mail into the app, feeding the same `/extract` flow. Push.

### Milestone 3 — Live Gmail (Phase 0 v1)
- **3.1 Google OAuth (read-only).** Server-side OAuth with the restricted Gmail read scope, testing mode, user as sole test user; tokens handled server-side. Push.
- **3.2 Fetch & extract.** Pull recent/unread messages, run them through `/extract`, present candidates for review. Push.
- **3.3 Privacy guardrails.** Enforce recent/unread-only, explicit in-app consent copy, and excerpt-only retention. Push.

### Milestone V — Voice-loop spike (optional, isolated, throwaway)
- **V.1** In a separate `spike/voice/` directory clearly marked throwaway, prove an end-to-end record → STT → LLM → TTS → play loop and measure time-to-first-audio on a real device against the spec's latency budget (§13). **Not wired into the app.** Push as a spike; do not let it touch feature code.

Stop after each milestone and confirm with the user before starting the next.

---

## 8. Your first actions (do these now, in order)

1. Confirm the **still-to-confirm items in §9** with the user (the GitHub remote and the bundle-ID handle) before writing feature code.
2. Do **Increment 0.1**: bootstrap the repo and, as part of it, create:
   - **`CLAUDE.md`** containing: the committed stack (§3), the invariants (§6), the git cadence and Conventional Commits rule (§5), the scope guardrails (§2), and the model-orchestration pattern (§11). This is the persistent memory for future sessions.
   - **`PROGRESS.md`** containing the full increment checklist from §7 with unchecked boxes.
3. Push, then report back and proceed to 0.2.

---

## 9. Decisions (confirmed with the user)

- **App name:** **Atlas**. Use a matching original bundle identifier in reverse-domain form, e.g. `com.<user-handle>.atlas` (confirm the handle/prefix with the user in Increment 0.1; pick a form the user is willing to keep, since the bundle ID is annoying to change later).
- **Repository visibility:** **private for now**, to be made public later. Do not add anything that assumes a public repo (no public README badges, no assumptions about public CI minutes) that would need undoing.
- **Branch strategy:** **PR-per-increment.** Each increment goes on a short-lived branch and merges into `main` via a pull request (`gh pr create`), self-merged once green. Keep the PR history clean and readable — it is part of the portfolio value.
- **LLM (starting):** **GPT-5 mini**, behind a swappable model interface (§3). Escalate only if extraction quality demands it; log per-model results (Increment 1.2) to make any future switch evidence-based.
- **Minimum iOS version:** **iOS 26.** Target modern SwiftUI and current-generation APIs freely; no backward-compatibility shims for older iOS are needed.

**Confirmed at kickoff:**
- **GitHub remote:** `https://github.com/P1Tan/atlas.git` (private repo, created manually by the user).
- **Bundle ID prefix / developer handle:** GitHub username `P1Tan` → bundle ID `com.p1tan.atlas`.

---

## 10. Event schema (first cut — refine in Increment 1.1)

Extraction returns a JSON array of Event objects (possibly empty). The model fills `title`, `date_phrase`, `location`, `source_excerpt`, `confidence`, `ambiguities`; backend code fills the resolved dates.

```json
{
  "title": "string — concise event title",
  "date_phrase": "string — the temporal expression exactly as found, e.g. 'next Thursday 3pm'",
  "resolved_start": "ISO8601 datetime | null — filled by code, not the model",
  "resolved_end": "ISO8601 datetime | null — filled by code; null if unknown",
  "all_day": "boolean",
  "location": "string | null",
  "notes": "string | null",
  "source_excerpt": "string — the sentence(s) the event was derived from, for user verification (not the full email)",
  "confidence": "high | medium | low",
  "ambiguities": ["string — e.g. 'year not specified', 'two possible times'"],
  "needs_confirmation": "boolean — always true in v1"
}
```

Reference datetime and timezone are passed into `/extract` so date resolution is deterministic and testable.

---

## 11. Model orchestration (architect / manager pattern)

Work in an **orchestrator + workers** pattern using Claude Code's subagent/Task mechanism, matching model tier to task difficulty rather than running everything on one model.

- **Architect/manager = highest available tier model.** Run the orchestrating role on the strongest model your current plan/credentials make available. This role owns the judgment-heavy work: reading and interpreting the spec, planning and decomposing each increment, making architectural and design decisions, handling the genuinely tricky logic (e.g. date resolution, anything touching the invariants in §6), reviewing every subagent's output, and owning the git workflow (§5) and Definition of Done (§12).
- **Delegate smaller, well-scoped tasks to appropriately-tiered subagents.** Spawn cheaper/faster models for mechanical or bounded work — boilerplate, scaffolding, routine file edits, writing tests to a given spec, repetitive refactors, simple lookups. Give each subagent a tight, self-contained brief.
- **Match the tier to the task.** Reserve the top model for design, security/privacy-sensitive code, and final review; push routine execution down to lower tiers. The goal is good judgment where it matters and efficiency everywhere else.
- **The orchestrator is accountable for everything committed.** Subagent output is **reviewed by the orchestrator before it is committed** — never pushed blindly. Invariants (§6), the confirm-before-write rule, and secret-handling apply to subagent-produced code exactly as to any other.
- **Don't over-engineer the hierarchy.** For a trivial increment, it's fine to just do it directly. Spin up the delegation pattern when an increment is big enough to actually benefit. If only one model tier is available, proceed normally.
- **Two distinct "models" — do not conflate.** The tiered models here are the ones Claude Code uses to *build* Atlas. They are separate from **Atlas's own runtime brain (GPT-5 mini, §3)**, which is the model the shipped app calls to extract events. Choosing a build-time tier never changes the app's runtime model, and vice versa.

---

## 12. Definition of done (per increment) — quick reference

- [ ] Implements exactly the current increment, nothing more
- [ ] Builds cleanly; tests/checks pass
- [ ] Manually verified (state how)
- [ ] Invariants (§6) upheld
- [ ] No secrets committed
- [ ] `PROGRESS.md` updated
- [ ] Conventional-commit message; committed and pushed
- [ ] Short summary reported to the user, with next increment named
