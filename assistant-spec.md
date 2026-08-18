# Atlas — Personal Assistant — Specification & Requirements

**Name:** Atlas
**Author:** [Your name]
**Status:** Draft
**Last updated:** [date]

---

## 1. Overview & vision

A personal, conversational assistant delivered as a mobile app. The user can either **speak** to it or **type**, and it responds in kind — with text always, and spoken audio when in voice mode. Beyond conversation, it can *act*: set reminders, answer questions, check the weather, search the web, and more, through a set of tools the language model can invoke. It remembers facts and preferences about the user so interactions feel personal rather than generic.

The vision is a "JARVIS-style" ambient assistant — capable, fast, a little personable — scoped realistically to a buildable core rather than an everything-app.

**Guiding principle:** nail one tight loop — fluid voice *and* text input, an LLM brain that can take real actions, and memory that makes it feel personal — before adding breadth.

---

## 2. Core experience

- **Two ways in, one brain.** Voice and text are both first-class inputs. Voice adds a speech-to-text front end that produces the same text a keyboard would; everything downstream is shared.
- **Conversational, with continuity.** It holds multi-turn context within a session and recalls relevant long-term facts across sessions.
- **It does things, not just says things.** The assistant can invoke tools (reminders, weather, search, etc.) and report results back naturally.
- **Personable.** A consistent persona and voice give it character without getting in the way.
- **Fast enough to feel alive.** Spoken replies begin quickly (see latency budget, §13) so a voice exchange feels like conversation, not a form submission.

---

## 3. Positioning — what it is and isn't

**Is:** a personal assistant that unifies natural conversation with real actions and memory, in one mobile app, controllable hands-free or by text.

**Isn't (by design, at least at first):** a replacement for Siri/Alexa/Google Assistant on every axis, a smart-home hub, or a proactive agent that acts unprompted. Those are explicitly later-phase or out of scope.

**Honest boundary.** The big platforms have enormous integration breadth this can't match. The wedge is a *personal, personalized, developer-owned* assistant: it remembers what matters to the user, has a persona they chose, runs the exact tools they wired up, and can use a more capable reasoning model than a default phone assistant. It competes on depth of personalization and capability, not breadth of integrations.

---

## 4. Entry points & cold-open strategy

**The problem.** The native OS assistant (Siri, Google Assistant) is summonable with zero app-launch — a side-button hold, a wake word, a lock-screen mic. A third-party app cannot claim those reserved entry points, so by default it loses the convenience race: unlock → find app → open → talk is far more friction than the incumbent, and habit favors whatever is already one gesture away. This is the single biggest threat to the app being used at all.

**The strategy.** Do not try to out-summon the native assistant head-on — that race is not winnable within OS limits. Instead: (1) reclaim every low-friction entry point the OS *does* permit, and (2) make the remaining friction worth paying by being visibly better at a signature job. Shortcuts get the user in; the wedge brings them back.

**Entry-point ladder (best-to-worst friction, all available to a third-party app):**

- **Home-screen widget with a mic button.** Visible on the home screen and one tap away — no app-drawer hunt. Deep-links straight into listening mode. The single highest-value lever: collapses "find app → open → press talk" into one tap on a surface the user already sees.
- **Action Button / Control Center / Shortcut deep-link.** iOS Action Button (15 Pro+) and Control Center, and Shortcuts/Quick Settings on both platforms, can launch directly into the listening state. A genuine hardware/near-lock-screen entry that power users configure once.
- **System share-sheet & assistant intents.** Register as a share target and (Android) integrate with assistant intents, so other apps become doorways in.
- **Persistent notification / Quick Settings tile.** An ongoing notification with a "talk" action, or an Android quick tile, gives an always-reachable tap target without opening the app first.
- **Launch-to-listen (baseline).** Opening the app drops *straight into listening* — no in-app home screen, no button to find. Cold-open means "app opens already hearing you," removing one of the two gestures.

**The differentiation wedge (what makes the extra gesture worth it).** A rational user won't pay *extra* friction over Siri without an obvious payoff, so the app needs one signature job it does visibly better — something the native assistant genuinely fumbles: complex multi-step reasoning, deep cross-session memory ("what did I say about the Henderson project last week?"), or a chosen vertical it goes deep on (study buddy, workout coach, travel brain). The cold-open cost is paid gladly once the user learns *"for THIS kind of thing, opening this is worth it."* This wedge is chosen and built in Phase 1, not deferred.

**Habit trigger.** Habits form around a cue. If the assistant owns a recurring moment (a morning brief on widget-tap, an end-of-day capture), the widget becomes the cue and cold-open stops being cold — it's part of a routine.

**Honest platform framing.** v1 optimizes the entry points the OS permits (widget, Action Button, launch-to-listen) and competes on capability, not on out-summoning the native assistant. Accepting this constraint openly is more credible than hand-waving it.

**MVP sequencing:** launch-to-listen + home-screen widget are must-haves; Action Button / Shortcut deep-link is a fast follow; the signature-capability wedge is built in Phase 1 so there is a real reason to pay the remaining friction.

---

## 5. Key design decisions

### 5.1 Large cloud LLM as the brain (with function calling)
An assistant needs broad reasoning and language ability, so the brain is a capable hosted model accessed via API, using **function/tool calling** to trigger actions. This is a deliberate reversal from a narrow-task project where a small local model would win: here, breadth and reasoning matter more than per-call cost or on-device execution. The model sits behind a swappable interface (§15); the **starting choice is GPT-5 mini** — the first feature (email-to-calendar extraction) is a bounded structured-output task where a budget-tier model is more than sufficient, and the cost of an eventual escalation is one line.

### 5.2 Voice is a streaming pipeline
Speech-to-text (STT) → LLM → text-to-speech (TTS), designed to **stream** so audio playback can begin before the full reply is generated. Minimizing time-to-first-audio is the central engineering challenge of the project.

### 5.3 One pipeline, two front ends
Text and voice converge immediately: the STT output is the same string the text box produces. Only the input capture and the optional spoken output differ. This keeps the core logic single-sourced.

### 5.4 Thin backend server (required, not optional)
API keys must never ship inside the mobile app (they are trivially extractable). A lightweight backend holds all provider keys, orchestrates the STT/LLM/TTS pipeline, executes tools, and stores memory. The mobile app talks only to this backend.

### 5.5 Layered memory
Short-term (in-session conversation context) plus long-term (durable user facts and preferences) retrieved into context when relevant. Memory is what turns a generic chatbot into *your* assistant.

---

## 6. System architecture

The pipeline is orchestrated by a voice-agent framework (see §6.1) rather than hand-assembled from raw services. Stages are streamed and overlapped — the LLM starts generating before transcription is fully final, and TTS starts speaking before the LLM finishes — which is what makes the latency budget (§13) achievable. The diagram shows the logical flow; in practice the stages run concurrently, not strictly in sequence.

```
─────────────────────────── MOBILE APP ───────────────────────────
│  Voice (mic) <-> real-time transport (WebRTC / WS)   Text input   │
│       │                     ▲                            │        │
└───────┼─────────────────────┼─────────────────────────────┼───────┘
        │  audio (streamed)   │  audio back (streamed)      │ text
        ▼                     │                             ▼
─────────────── BACKEND: voice-agent framework ────────────────────
│                                                                   │
│   VAD / turn-detection                                            │
│        │                                                          │
│        ▼         (streamed, overlapping stages)                   │
│   STT ──▶ LLM (brain + function calling) ──▶ TTS ──▶ audio out    │
│             │        ▲                                             │
│    tool call│        │ tool result       retrieve / write         │
│             ▼        │                        ▲ │                  │
│   ──────────────────────            ─────────┼──────────         │
│   │ Tools / integrations │            │  Memory store    │         │
│   │ reminders, weather,  │            │ profile + vector │         │
│   │ search, calendar…    │            └──────────────────┘         │
│   ──────────────────────                                         │
│                                                                    │
│   ── per-stage timing telemetry taps every stage (§13) ──          │
─────────────────────────────────────────────────────────────────────
        ▲                                             │
        │ text turns share the same LLM + tools + memory
        └─────────────────────────────────────────────┘

Alternative (evaluated, see §6.2): replace STT+LLM+TTS with a single
speech-to-speech realtime model (audio in -> audio out, one connection).
```

### 6.1 Architecture decision: framework-orchestrated pipeline

The real-time plumbing of a voice app — capturing streamed mic audio, voice-activity detection, turn-taking, barge-in, overlapping the stages, swapping providers — is months of undifferentiated infrastructure work. Rather than hand-build it, the project uses an open-source voice-agent framework that provides this orchestration and exposes each stage (STT, LLM, TTS, tools, memory) as a swappable component. This keeps the pipeline *visible and tunable* — so latency can be measured and optimized per stage, and providers compared — while avoiding the raw WebRTC/VAD work.

**Committed choice (v1):** **Pipecat** as the pipeline framework, running on a **LiveKit transport**, with a **native iOS (SwiftUI) client** using LiveKit's Swift client SDK. Rationale: Pipecat gives the clean, swappable, measurable pipeline that makes latency the visible engineering story (matching this project's core thesis); LiveKit underneath supplies the production-grade mobile transport and a native iOS SDK, so the app–agent audio link is solved rather than assembled. Because v1 is iOS-only (§11), a cross-platform abstraction (React Native / Flutter) no longer earns its keep — and the low-friction entry points are native iOS work regardless — so a native SwiftUI shell is the simpler path. The alternative of using LiveKit Agents as the *framework* itself (rather than just transport) was considered; Pipecat was preferred for the explicit, tunable pipeline.

### 6.2 Alternative considered: speech-to-speech realtime model

A single audio-in / audio-out model (e.g. OpenAI Realtime API, Gemini Live) collapses STT+LLM+TTS into one streaming connection, with lower latency and native barge-in and interruption handling. It was evaluated and **not** chosen for v1, deliberately: it is more of a black box (less to demonstrate about the pipeline engineering that is the point of this project), audio always leaves the device (a privacy cost), and per-minute audio pricing is higher. It remains a strong later-phase option, and a known hybrid — run the realtime model for turn-detection and reasoning but emit *text*, then send that to a specialist TTS for voice quality — is noted for Phase 2+. Documenting this trade-off is itself part of the design rationale.

---

## 7. The voice + text pipeline

- **Input capture.** Voice mode streams mic audio to the backend over the real-time transport (push-to-talk for v1; wake-word is later-phase). Text mode captures a typed string sent over a normal request. Both converge on the same orchestration.
- **VAD / turn-detection.** The framework detects speech, endpoints the user's turn (when they've stopped talking), and manages turn-taking — plumbing the framework provides rather than code the project writes.
- **STT.** Audio is transcribed to text. Provider options: cloud (Deepgram, AssemblyAI) for fast streaming, or on-device (Apple Speech, Android SpeechRecognizer, whisper.cpp) for privacy/latency. Decision deferred (§18).
- **Orchestration.** The framework passes the text plus retrieved memory and the tool schema to the LLM. If the model requests a tool, the backend executes it and returns the result to the model, looping until a final reply. Stages stream and overlap to minimize latency.
- **Output.** The reply text returns to the app always. In voice mode it is also streamed through TTS and played back, beginning before the full reply is generated. Note: a good spoken reply (short, linear) differs from a good text reply (can be longer, structured), so output is adapted to the modality rather than emitted as one identical string.
- **Barge-in.** Letting the user interrupt playback by speaking is provided by the orchestration framework (and is native to the speech-to-speech alternative in §6.2), so it can be included earlier than a hand-built pipeline would allow rather than deferred to a late phase.

---

## 8. Capabilities (tools)

**MVP tool set (v1):**

- **Email-to-calendar (first tool to build — see §8.1)**
- Set / list / cancel reminders and timers
- Current weather and forecast
- General question answering (via the LLM)
- Web search for fresh information
- Store / recall a personal fact ("remember that…")

**Later-phase:**

- Send a message or email (with confirmation)
- Notes / task-app integration
- Unit conversions, math, quick utilities
- Smart-home control (large scope; explicitly later)

Each tool is a backend function with a typed schema the LLM can call. Adding a capability = adding a tool, which keeps growth incremental.

### 8.1 Email-to-calendar (the first feature)

Turns scheduling information sitting in email into calendar events: extract the event(s) from a message, resolve dates, propose them for review, and write confirmed ones to the calendar. This is the **first feature to implement**, and it is deliberately built **text-first** — it exercises the LLM tool-calling loop, an integration, and a confirm-before-acting UX with *no dependency on the voice pipeline*, so it delivers a real, useful feature while the hardest voice work is still ahead. Voice later flows through it unchanged, since voice is only a front end to the same text.

**Scope note (honest positioning).** This is *one tool among several*, not the app's signature wedge (§4 remains to be chosen). Apple already ships per-item event detection from Mail/screenshots natively, so the naive "detect a date, add an event" is not a differentiator. The version worth growing toward — and the later-phase target — is inbox-*wide* reasoning: handling multi-event and cross-thread scheduling Apple's per-item detection misses, implicit/fuzzy scheduling, and proposing conflict-free times against the existing calendar. v1 does not need to beat Apple; it needs to be useful to the builder and to prove the pipeline.

**Build path (simplest first):**

- **v0 — no OAuth.** Receive a message via the iOS **share sheet** (forward/share from Mail) or a paste box. This exercises the full extract -> propose -> confirm -> write pipeline without touching Google's restricted-scope review.
- **Extraction.** The LLM (via tool calling) returns candidate events as structured data (title, start, end, location), resolving relative dates the same way the temporal-extraction pattern does elsewhere — extract the phrase as text, let deterministic code resolve it against the current date. Ambiguous items are flagged, not guessed.
- **Review + confirm.** Proposed events appear in a review UI where any field can be edited; a one-tap confirm is required before anything is written. Auto-creating from a misread email is the failure that erodes trust, so human-in-the-loop is a permanent feature, not a limitation.
- **Write.** Confirmed events are written via native **EventKit** (no second OAuth; works with whatever calendars are on the device).
- **v1 — live Gmail.** Add read-only Gmail access (OAuth restricted scope, testing mode with the builder as the sole user) so it can sweep recent/unread mail automatically instead of requiring a manual share. Note the restricted-scope security review gates any real distribution.

**Privacy note.** Email is among the most sensitive data a user has, and extraction sends message content to a cloud LLM. Process only selected or recent/unread mail — never the whole inbox by default — and be explicit about it. This restraint is also the seed of the smarter later-phase version (reason across recent mail + calendar together).

---

## 9. Memory & personalization

- **Short-term:** the running conversation, passed as context within a session.
- **Long-term facts:** durable user statements and preferences ("I'm vegetarian", "my sister's name is Maya", "I prefer metric"), stored and retrieved when relevant — via embeddings/semantic retrieval and/or a structured profile.
- **Write path:** the assistant can be told to remember things explicitly, and can also extract salient facts automatically (later-phase, with care to avoid storing noise).
- **User control:** the user can view and delete what's remembered — both an ethical requirement and a trust feature.

---

## 10. Persona

- A consistent character (tone, address style, a little dry wit) defined in a system prompt.
- A distinctive TTS voice that matches the persona (a good hosted TTS such as ElevenLabs/Cartesia is what sells the "JARVIS" feel).
- Persona is configuration, not hard-coding, so it can be tuned or swapped.

---

## 11. Mobile app requirements

- **Platform:** iOS only for v1 (native SwiftUI), **minimum iOS 26**. Android is a later phase. iOS-first removes the cross-platform tax, lets the app use native entry points and on-device APIs directly, and narrows the surface to one well-understood platform; a modern floor means current SwiftUI and current-generation APIs can be used without backward-compatibility shims.
- **Voice UI:** a clear press-to-talk control, live transcription feedback, and a visible state (listening / thinking / speaking).
- **Text UI:** a standard chat interface; text and voice turns share one conversation view.
- **Mode switching:** the user can move between voice and text mid-conversation without losing context.
- **Playback controls:** ability to stop/replay spoken responses.
- **Auth:** a simple account so memory and history are tied to the user.

**iOS-specific considerations (consequences of the iOS-only choice):**

- **Background mic is heavily restricted.** iOS does not allow a third-party app to keep the microphone always-on in the background, so a custom always-listening wake word ("Hey [name]") is effectively not feasible as a background service. The ambient "just speak into the room" fantasy is constrained on iOS specifically — plan the entry experience around foreground activation, not always-on listening.
- **App Intents / Siri hand-off is the sanctioned near-wake-word path.** Exposing the assistant's actions via **App Intents** lets the user invoke it through Siri and Shortcuts ("Hey Siri, ask [app] to…"), partially reclaiming a hands-free entry point without an always-on mic. This is the realistic substitute for a custom wake word on iOS and should be a Phase-1/2 entry point.
- **Native entry points are all iOS APIs.** Home-screen widget (WidgetKit), Action Button (iPhone 15 Pro+), Control Center controls (iOS 18+), and the App Intents above — each is native work, which is part of why v1 is native SwiftUI rather than cross-platform.
- **On-device STT is an iOS advantage.** Apple's Speech framework offers good on-device transcription, which for iOS-only can lower STT latency and cost and keep audio on-device — a real privacy and latency win worth evaluating against cloud STT.
- **Audio session management is non-trivial.** Handling interruptions (incoming calls, other apps' audio), the silent switch, Bluetooth/AirPods routing, and ducking via `AVAudioSession` is fiddly real work for any voice app; budget for it.
- **Reminders need real delivery.** Since reminders/timers are a core tool, delivering them requires local notifications (and/or push via APNs) with the right permissions — the tool isn't done when the LLM "sets" it; it's done when the phone actually alerts.
- **Mic launch needs a guard.** "Launch-to-listen" must show an unmistakable live-mic indicator and an instant cancel, so opening the app in the wrong moment (a meeting) is never a surprise-recording — both a UX and a trust requirement.
- **App Store review & privacy.** A mic-driven app needs a clear microphone usage description, accurate privacy-nutrition labels, and a review-safe justification for its mic use; distribution has real gating that a web project doesn't.

---

## 12. Functional requirements

- **FR1 — Dual input.** Accept both spoken and typed input; both must work.
- **FR2 — Transcription.** Convert speech to text accurately enough for reliable intent understanding.
- **FR3 — Conversational reply.** Produce coherent, context-aware replies over multiple turns.
- **FR4 — Spoken output.** In voice mode, speak replies via TTS; text is always shown.
- **FR5 — Tool invocation.** The LLM can call tools and incorporate their results into replies.
- **FR6 — MVP tools.** Reminders/timers, weather, web search, Q&A, and remember/recall are implemented.
- **FR7 — Memory.** Persist and retrieve long-term user facts across sessions.
- **FR8 — Memory control.** The user can view and delete stored memories.
- **FR9 — Mode continuity.** Switching between voice and text preserves conversation context.
- **FR10 — Persona.** Replies reflect a consistent, configurable persona and voice.
- **FR11 — Low-friction entry.** Provide launch-to-listen (app opens already listening) and a home-screen widget with a one-tap mic, per the cold-open strategy (§4).
- **FR12 — Signature capability.** Implement at least one clearly differentiated job the native assistant does poorly (§4), so the app is worth opening over the incumbent.

---

## 13. Non-functional requirements

- **NFR1 — Latency (the headline metric).** Target time-to-first-audio in voice mode under ~1.5 s. Illustrative budget:

  | Stage | Target |
  |---|---|
  | STT (end of speech → transcript) | ≤ 400 ms |
  | LLM (transcript → first token) | ≤ 600 ms |
  | TTS (first token → first audio) | ≤ 400 ms |
  | **Total to first audio** | **≤ ~1.4 s** |

  Achieved by streaming each stage rather than waiting for the previous to fully complete.
- **NFR2 — Reliability.** Graceful handling of STT errors, tool failures, and network loss (clear fallback messaging, no silent hangs).
- **NFR3 — Security.** No provider API keys in the mobile client; all secrets server-side.
- **NFR4 — Privacy.** Personal data and memory encrypted at rest; clear user control over stored data.
- **NFR5 — Platform (v1).** Ships on iOS (native SwiftUI), minimum **iOS 26**. Android is deferred to a later phase; v1 does not target a shared codebase.

---

## 14. Privacy & security

An assistant that "knows you" concentrates sensitive personal data, so this is first-class, not an afterthought:
- All third-party keys live on the backend; the app never holds them.
- Memory and conversation history are encrypted at rest and scoped to the authenticated user.
- The user can inspect and delete any stored memory or history.
- Be explicit that voice audio and text are sent to third-party providers (STT/LLM/TTS) for processing; consider on-device STT if minimizing that is a goal.
- No selling or secondary use of user data.

---

## 15. Tech stack (proposed)

The central choice is *not* to hand-assemble the voice pipeline. An open-source voice-agent framework provides the real-time orchestration (VAD, turn-taking, barge-in, streaming, provider-swapping) while keeping each stage swappable and measurable — preserving the engineering that matters here without months of raw real-time infrastructure work (see §6.1). The individual providers below are current best guesses in a fast-moving space; the durable decisions are the *architecture* (framework-orchestrated, swappable stages, specialist TTS, per-stage telemetry), not the specific vendors.

- **Orchestration framework:** **Pipecat** (Python-first, clean swappable pipeline, latency measurable per stage) — the committed v1 choice (§6.1).
- **Real-time transport:** **LiveKit**, which supplies the media layer and a native **iOS Swift client SDK** for the app–agent audio link.
- **Mobile:** **native iOS (SwiftUI)** for v1. Cross-platform abstraction is dropped since v1 is iOS-only and the entry points (widget, Action Button, Control Center, App Intents) are native work regardless. Android is a later phase.
- **LLM (brain):** a hosted, tool-calling model kept model-agnostic behind the pipeline so it can be swapped. **Starting model: GPT-5 mini** (budget tier, ample for structured extraction); escalate to a stronger model only if quality demands it, and log per-model results to make any switch evidence-based.
- **STT:** Deepgram or AssemblyAI for fast streaming; **on-device Apple Speech** is a strong iOS-only option that cuts latency and cost and keeps audio on-device (see added considerations). Decision deferred (§18).
- **TTS (where the persona lives):** ElevenLabs Flash for low latency; Cartesia or Inworld as alternatives. Spend the quality budget here — this layer sells the voice.
- **Memory:** start with a structured profile table in Postgres; add pgvector for semantic recall only when actually needed. No dedicated vector DB required on day one.
- **Auth / data:** Supabase or Firebase (auth + Postgres + storage, pgvector available in Supabase) to cut boilerplate.
- **Backend hosting:** Fly.io, Railway, or Render for the Python/Pipecat agent; LiveKit Cloud or self-hosted LiveKit for the media server.
- **Observability:** per-stage timing telemetry from day one (structured logs or a tracing tool), wired into Pipecat's stage hooks — this is the evidence behind the latency budget (§13), not an afterthought.
- **Alternative primitive (evaluated, §6.2):** a speech-to-speech realtime model (OpenAI Realtime API / Gemini Live) as a later-phase option or hybrid, consciously not chosen for v1.

---

## 16. Scope & phasing

**Phase 0 — First milestone (build order, text-first):**
Build the **email-to-calendar tool (§8.1) text-first**, before the voice pipeline exists: the LLM tool-calling loop, extraction with date resolution, the review/confirm UI, and EventKit writes — all driven by typing, starting from the share-sheet/paste v0. In parallel, do a **throwaway spike of the voice loop** (record -> STT -> LLM -> TTS -> play, however rough) purely to confirm the latency budget (§13) is achievable on a real device — proving the scariest unknown without letting it block the first useful feature. This slice is the recommended starting point because it yields a real, working feature while deferring the hardest voice work.

**Phase 1 — MVP (the core that must feel magic):**
Text + push-to-talk voice, one LLM brain with function calling, the MVP tool set (§8, email-to-calendar plus reminders, weather, Q&A, web search, remember/recall), basic long-term memory with user control, a consistent persona and TTS voice, on iOS only. Latency tuned to hit §13. Includes the low-friction entry points that make it openable (launch-to-listen + home-screen widget) and the one signature capability that makes it worth opening (§4 — still to be chosen; email-to-calendar is a useful tool but not that wedge) — these are core to the MVP, not polish, because they determine whether the app gets used at all.

**Phase 2 — Depth:**
Inbox-*wide* email reasoning (multi-event, cross-thread, conflict-free time proposals — the smarter version of §8.1), messaging tools (with confirmation), automatic fact extraction, richer memory retrieval, replay/history, wake-word activation.

**Phase 3 — Ambient / advanced:**
Barge-in interruption, proactive suggestions, smart-home or additional integrations, multi-device continuity.

Ship each phase as a working, demoable slice before starting the next. A tight working core beats a broad broken one.

---

## 17. Success criteria

- A user can complete a full task hands-free by voice (e.g. "remind me to call the dentist tomorrow at 10") and by text, in the same conversation.
- Voice replies begin within the §13 latency target on a real device.
- The assistant correctly recalls a fact stated in a previous session.
- The five MVP tools work end-to-end, including graceful failure.
- A first-time user can hold a natural multi-turn exchange without instruction.

---

## 18. Risks & open questions

- **Latency is the biggest technical risk.** Streaming across the pipeline and a mobile network is genuinely hard; treat the latency budget as the core engineering problem, and prototype the voice loop first to de-risk it.
- **Cost & abuse guardrails.** Hosted LLM + STT + TTS per interaction adds up, and a 24/7 backend has standing cost; add per-user rate limits and usage caps so a bug or abuse can't run up the bill. Frame the project honestly as a portfolio piece rather than a commercial product with worked-out unit economics.
- **Provider data handling.** Audio and text pass through third-party STT/LLM/TTS providers; check each provider's retention settings (some offer zero-retention) and state this in the privacy posture (§14).
- **Voice UX edge cases.** Noisy environments, accents, when to stop listening — plan test coverage. Test on a real device; the simulator can't validate mic behavior or latency.
- **Scope creep.** The vision invites endless features; the phasing in §16 is the guardrail.
- **Open question:** STT on-device (Apple Speech) vs cloud (Deepgram/AssemblyAI) — trades privacy and latency against accuracy and effort. iOS-only makes on-device genuinely viable (§11).
- **Open question:** memory design — pure semantic retrieval vs a structured user profile vs both.
- **Resolved for v1 (was an open question):** a custom always-on wake word is not feasible as a background iOS service (§11); hands-free entry is instead approached via App Intents / Siri hand-off plus the Action Button, and always-on wake-word is left to a possible Android or later phase.
