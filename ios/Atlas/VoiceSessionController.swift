import AVFoundation
import Foundation
import LiveKit
import UIKit

/// Milestone 7.4b: drives a full push-to-talk voice turn against the
/// backend's Pipecat/LiveKit voice pipeline -- fetches a LiveKit join token
/// (`POST /voice/token`), joins the room, runs on-device STT
/// (`VoiceTranscriptionService`, unchanged from 7.2b), publishes transcript/
/// turn-boundary data messages, and (new in 7.4b) receives the backend's
/// `assistant_reply`/`tool_result` data messages and taps the subscribed
/// remote audio track's PCM so the most recent reply can be replayed locally.
///
/// Deliberately not an `ObservableObject`: `ChatViewModel` owns one of these
/// as a plain private property and drives its own `@Published` UI state from
/// the callback closures below, so the chat transcript and voice turn share
/// one source of truth (`ChatViewModel.messages`) instead of two competing
/// view models.
///
/// `@MainActor`-isolated for simplicity, matching `ChatViewModel`'s own
/// isolation and the original 7.2b `VoiceSessionViewModel`. `RoomDelegate`'s
/// documented contract is that its methods are "not guaranteed to be called
/// on the main thread" (see `RoomDelegate.swift`'s doc comment in the
/// installed SDK source) -- the two delegate methods implemented below are
/// therefore marked `nonisolated` and hop onto the main actor themselves
/// before touching any of this class's state, rather than assuming the SDK
/// happens to call in on main.
@MainActor
final class VoiceSessionController: NSObject, @unchecked Sendable {
    enum SessionState: Equatable {
        case idle
        case connecting
        case listening
        /// Transitional: set synchronously (before any `await`) at the top
        /// of `stopVoiceTurn()`/`cancelVoiceTurn()`, distinct from any value
        /// either method's own entry guard accepts. Closes the reentrancy
        /// window a rapid double-tap could otherwise hit -- a second
        /// near-simultaneous call sees `.stopping` (not `.listening`/
        /// `.connecting`) and returns early via the guard, instead of both
        /// calls racing into `teardownRoom()`/`transcriptionService.stop()`
        /// concurrently.
        case stopping
        /// The user's utterance has ended (mic capture stopped) but the
        /// room is deliberately still connected: the assistant's reply
        /// (`assistant_reply` text, `tool_result`, and TTS audio) can only
        /// arrive over this same LiveKit room, and generating it takes a
        /// full LLM+TTS round trip -- far longer than the brief drain
        /// `stopVoiceTurn()` waits on. The room is only torn down once the
        /// reply has genuinely finished (see `finishReplyIfComplete()`) or
        /// `cancelVoiceTurn()` is called explicitly.
        case awaitingReply
        case stopped
    }

    // MARK: - Callbacks (set once by ChatViewModel at init)

    var onSpeechStarted: (() -> Void)?
    var onInterimTranscript: ((String) -> Void)?
    var onFinalTranscript: ((String) -> Void)?
    var onSpeechStopped: (() -> Void)?
    var onAssistantReply: ((String) -> Void)?
    var onToolResult: ((ToolResultMessage) -> Void)?
    /// Fired when the assistant's spoken reply audio starts arriving/playing.
    var onPlaybackStarted: (() -> Void)?
    /// Fired when playback of the current reply's audio has gone quiet
    /// (finished, or was stopped/replaced).
    var onPlaybackStopped: (() -> Void)?
    /// Fired exactly once a voice turn's reply has completed entirely
    /// normally: both the assistant's text and its full spoken audio
    /// arrived, then genuinely went quiet -- NOT the timeout/pipeline-error/
    /// interruption paths that also tear the room down (see
    /// `finishVoiceSessionAfterReply`'s `replyCompletedSuccessfully` param).
    /// Never fires for an explicit user cancel either -- `cancelVoiceTurn()`
    /// tears the room down directly and never reaches that method at all.
    /// Exists so a caller can auto-start a new turn only after a real,
    /// successful reply, continuous-conversation-style, without also
    /// auto-restarting into a mic session right after a failure.
    var onReplyCompleted: (() -> Void)?
    /// Fired whenever a voice session ends WITHOUT a successful reply: the
    /// 30s no-reply timeout, a `pipeline_error` message, an interruption
    /// while `.awaitingReply` (every case where `finishVoiceSessionAfterReply`
    /// runs without `replyCompletedSuccessfully`), and an interruption while
    /// `.listening`/`.connecting` (via `cancelVoiceTurn()`, see
    /// `endActiveSessionForInterruption`). `onError` above already carries
    /// the human-readable reason for all of these; this exists purely so a
    /// caller can still reset its own UI state (e.g. `ChatViewModel.voiceState`)
    /// even when nothing else on these paths does -- without it, a caller
    /// left showing "waiting for a reply" or "Listening…" has no way to
    /// notice the session actually ended.
    var onReplyFailed: (() -> Void)?
    var onError: ((String) -> Void)?

    private(set) var state: SessionState = .idle

    private let baseURL = AtlasAPI.baseURL
    private var room: Room?
    private let transcriptionService = VoiceTranscriptionService()
    private var eventTask: Task<Void, Never>?

    /// Set by `cancelVoiceTurn()` to suppress forwarding/publishing any
    /// events still draining out of `transcriptionService` after it's told
    /// to stop -- the whole point of cancel is that nothing captured so far
    /// gets sent anywhere.
    private var isCancelled = false

    // MARK: - Remote audio capture (for replay)

    private let replayRenderer = ReplayAudioRenderer()
    private var currentAudioTrack: RemoteAudioTrack?
    private var lastReplyBuffers: [AVAudioPCMBuffer] = []
    /// Debounce timer for the "playback has gone quiet" heuristic: there is
    /// no explicit "reply audio finished" message in the current backend
    /// protocol, so -- exactly like `VoiceTranscriptionService`'s own
    /// RMS/silence-window heuristic for the *user's* turn boundary -- this
    /// treats a gap with no new PCM frames as the reply having finished.
    ///
    /// Corrected after live diagnostic logging: that comment's original
    /// premise -- "a gap with no new PCM frames" -- turned out to be false.
    /// WebRTC delivers a continuous, essentially unbroken stream of frames
    /// (confirmed: every ~10ms, zero gap) for as long as the remote track
    /// stays subscribed, regardless of whether the assistant is actually
    /// saying anything -- a real design property of the transport, not a
    /// bug. `lastAudioFrameTime` is therefore updated only from frames
    /// whose content clears `replySilenceThreshold` below, NOT from every
    /// frame that merely arrives -- see `audioFrameReceived`.
    private var quietCheckTimer: Timer?
    private var lastAudioFrameTime: Date?
    private let playbackQuietWindow: TimeInterval = 1.0
    /// Peak-amplitude (0...1) floor a frame's content must clear to count as
    /// real speech rather than the continuous low-level/comfort-noise
    /// stream WebRTC keeps delivering during silence. A first-pass estimate,
    /// not yet validated against real device data the way the mic-input
    /// threshold was (`VoiceTranscriptionService.silenceThreshold`) -- the
    /// diagnostic logging that found this bug is left in place specifically
    /// so this can be recalibrated from real numbers if it turns out wrong,
    /// same as that earlier investigation.
    private let replySilenceThreshold: Float = 0.02

    // MARK: - Reply-completion / room-teardown bookkeeping (Finding 0)

    /// Set once the current turn's `assistant_reply` text message has
    /// arrived. Reset at the start of every new voice turn.
    private var replyTextReceived = false
    /// Set once the reply's audio has gone quiet (`checkForQuiet()`), and
    /// cleared again if audio then resumes (a natural mid-sentence pause,
    /// not the reply actually finishing -- see `audioFrameReceived()` and
    /// Finding 4). The room is only a candidate for teardown while this is
    /// `true`.
    private var replyAudioFinished = false
    /// Scheduled once both `replyTextReceived` and `replyAudioFinished` are
    /// true. Deliberately NOT torn down immediately: `checkForQuiet()`'s
    /// 1.0s `playbackQuietWindow` can fire on an ordinary mid-sentence pause
    /// in the assistant's speech, and if the room were disconnected right
    /// then, any resumed audio for the SAME reply would have nowhere to
    /// arrive. This extra grace period gives resumed audio a chance to
    /// cancel the pending teardown (see the `wasQuiet` branch of
    /// `audioFrameReceived()`) before the connection is actually severed.
    private var pendingTeardownTask: Task<Void, Never>?
    private let roomTeardownGraceWindow: TimeInterval = 1.5
    /// Safety net: if the backend never sends a text reply and/or never
    /// starts a track for this turn (e.g. a pure tool-calling round with no
    /// spoken reply -- confirmed possible per
    /// `voice_assistant_reply_bridge.py`'s doc comment), `replyTextReceived`
    /// / `replyAudioFinished` could otherwise never both become `true` and
    /// the room would stay connected forever. This unconditionally finishes
    /// the session a fixed time after the *last sign of life*, so the state
    /// machine can never get stuck in `.awaitingReply`.
    ///
    /// History (why this is an `ExtendableDeadline`, not a flat `Task.sleep`):
    /// found live that a flat, never-reset 30s timeout cut off long replies
    /// mid-playback (a full LLM+TTS+playback round trip can easily exceed
    /// 30s while working correctly); found live AGAIN, immediately after
    /// making it reset-on-activity, that an unreset-able reset is exactly as
    /// dangerous as no timeout at all -- a stuck genuine-quiet detector (the
    /// render tap kept receiving a trickle of frames well past when the
    /// reply actually ended, confirmed with the app held in the foreground
    /// the whole time) pushed the deadline out forever. `ExtendableDeadline`
    /// is the general shape both fixes needed at once: `extend()`-able, but
    /// never past `maxAwaitingReplyDuration` from the original `start()`.
    private lazy var awaitingReplyDeadline = ExtendableDeadline(
        duration: awaitingReplyTimeout, maxTotalDuration: maxAwaitingReplyDuration
    ) { [weak self] in
        guard let self, self.state == .awaitingReply else { return }
        // Milestone 9.1 (NFR2): this firing while still `.awaitingReply`
        // means total silence -- no `pipeline_error` message arrived either
        // (that path calls `finishVoiceSessionAfterReply()` directly, which
        // cancels this deadline before it ever gets here), so nothing has
        // told the user what happened yet.
        if !(self.replyTextReceived && self.replyAudioFinished) {
            self.onError?("Atlas didn't reply in time. Please try again.")
        }
        await self.finishVoiceSessionAfterReply()
    }
    private let awaitingReplyTimeout: TimeInterval = 30.0
    /// Hard ceiling on total time spent in `.awaitingReply`, independent of
    /// `checkForQuiet()`'s `extend()` calls: generous enough for a
    /// genuinely long spoken reply (the original problem this whole
    /// mechanism exists to tolerate), but finite, so a stuck genuine-quiet
    /// detector can never hang the session indefinitely.
    private let maxAwaitingReplyDuration: TimeInterval = 90.0

    // MARK: - Listening idle timeout (LiveKit connection-minute cost control)

    /// Nothing in this state machine used to bound `.listening`. LiveKit
    /// Cloud bills participant connection minutes, and continuous-
    /// conversation mode auto-starts a fresh turn after every reply, so a
    /// user who simply walked away left BOTH participants connected (mic
    /// hot, room open) until the join token's 1h TTL finally expired -- the
    /// single largest source of billed-but-unused minutes in the per-turn
    /// room design.
    ///
    /// This bounds exactly that case: a listening turn during which no
    /// speech was ever detected. Started when `.listening` is entered
    /// (`startVoiceTurn()`), cancelled on the first `.speechStarted`
    /// (`handle(_:)`) -- from then on the turn is already bounded by the
    /// user's own utterance plus `maxAwaitingReplyDuration` -- and in
    /// `teardownRoom()` alongside `awaitingReplyDeadline`.
    ///
    /// Deliberately does NOT auto-restart listening the way a successful
    /// reply does (`onReplyCompleted`): re-arming the mic after a silence
    /// timeout would put the room straight back into the exact state this
    /// exists to end.
    ///
    /// Reuses `ExtendableDeadline` rather than hand-rolling another
    /// `Task.sleep`, with `maxTotalDuration` equal to `duration`: nothing
    /// ever calls `extend()` on this one (detected speech cancels it
    /// outright rather than pushing it out), so the cap can't matter --
    /// matching the two values keeps that explicit instead of implying some
    /// extension budget exists.
    private lazy var listeningIdleDeadline = ExtendableDeadline(
        duration: listeningIdleTimeout, maxTotalDuration: listeningIdleTimeout
    ) { [weak self] in
        guard let self, self.state == .listening else { return }
        // Soft, non-alarming wording on purpose: nothing failed here, the
        // session was just closed so an unattended room stops costing money.
        self.onError?("Stopped listening after a while of silence. Tap the mic when you're ready.")
        await self.cancelVoiceTurn()
        // Same gap `endActiveSessionForInterruption` closes on its own
        // cancel path (see `onReplyFailed`'s doc comment): `cancelVoiceTurn()`
        // never tells a caller the session ended, so without this
        // `ChatViewModel.voiceState` stays at `.listening` and the mic button
        // keeps showing a turn that is already over.
        self.onReplyFailed?()
    }
    /// How long `.listening` may run without a single detected utterance
    /// before the turn is ended. Long enough not to cut off someone who
    /// tapped the mic and is still gathering their thoughts, short enough
    /// that an abandoned session costs seconds of connection time instead of
    /// the full hour the token would otherwise allow.
    private let listeningIdleTimeout: TimeInterval = 45.0

    // MARK: - Data-channel heartbeat (dead-channel detection)

    /// Found live: LiveKit's reliable data channel -- the one every
    /// `publish()` call in this file goes over -- was observed silently
    /// dying mid-session (`publisher data channel '_reliable' closed
    /// unexpectedly` at the transport layer). Once that happens,
    /// `transcriptionService` keeps detecting real speech locally and
    /// `publish()` keeps returning normally (never throws), but literally
    /// nothing reaches the backend -- indistinguishable from "the mic
    /// stopped working" from the user's side, and previously only
    /// discoverable after minutes (via the backend's own 5-minute idle
    /// timeout). This is an app-level ping/pong probe specifically because
    /// there is no lower-level signal available: neither this SDK version
    /// nor a thrown error from `publish()` surfaces the failure.
    private var heartbeatTask: Task<Void, Never>?
    private var lastPongReceivedAt: Date?
    private let heartbeatInterval: TimeInterval = 5.0
    /// Roughly 2 missed round trips' worth of tolerance before declaring
    /// the channel dead -- long enough to absorb ordinary network jitter,
    /// short enough that detection lands in ~15s worst case rather than the
    /// minutes it took before this existed.
    private let heartbeatTimeout: TimeInterval = 12.0

    // MARK: - Local replay playback

    private var playbackEngine: AVAudioEngine?
    private var playbackPlayerNode: AVAudioPlayerNode?

    /// The backend's voice agent always joins its LiveKit room with this
    /// fixed, known identity -- confirmed in `backend/app/voice_agent.py`:
    /// `generate_token_with_agent(..., participant_name: "atlas-voice-agent",
    /// ...)`. MUST be kept in sync with that value. `assistant_reply`/
    /// `tool_result` data messages are only acted on when the sender's
    /// identity matches this exactly (Finding 1, security review): without
    /// this check, any other participant able to join the shared dev room
    /// could forge a `tool_result` for `set_reminder` and get iOS to
    /// schedule an arbitrary real local notification, or inject fake
    /// assistant text straight into the chat transcript.
    private let expectedAgentIdentity = "atlas-voice-agent"

    private static let responseDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    /// FastAPI's `HTTPException(detail: ...)` shape (`{"detail": "..."}`).
    /// `nil` on any decode failure -- callers fall back to a generic message
    /// rather than surfacing a decode error for what's already an error.
    private static func decodeErrorDetail(from data: Data) -> String? {
        struct ErrorBody: Decodable { let detail: String }
        return try? JSONDecoder().decode(ErrorBody.self, from: data).detail
    }

    // MARK: - Audio session interruption / route-change handling (Milestone 9.2, §11)

    private var interruptionObserver: NSObjectProtocol?
    private var routeChangeObserver: NSObjectProtocol?
    /// Not an audio-session observer like the two above, but registered and
    /// torn down with them -- see `observeAudioSessionEvents()`.
    private var didEnterBackgroundObserver: NSObjectProtocol?
    /// Guards `endActiveSessionForInterruption` against firing twice for one
    /// real-world event -- a phone call can plausibly trigger both
    /// `interruptionNotification` and a `.oldDeviceUnavailable` route change
    /// in quick succession.
    private var isEndingSessionForInterruption = false

    override init() {
        super.init()
        replayRenderer.onFrame = { [weak self] peak in
            Task { @MainActor in self?.audioFrameReceived(peakAmplitude: peak) }
        }
        observeAudioSessionEvents()
    }

    deinit {
        if let interruptionObserver {
            NotificationCenter.default.removeObserver(interruptionObserver)
        }
        if let routeChangeObserver {
            NotificationCenter.default.removeObserver(routeChangeObserver)
        }
        if let didEnterBackgroundObserver {
            NotificationCenter.default.removeObserver(didEnterBackgroundObserver)
        }
    }

    /// Registered once for this controller's whole lifetime (it's owned by
    /// `ChatViewModel`, which effectively lives as long as the app does),
    /// rather than per-turn -- simpler than trying to add/remove precisely
    /// around each `startVoiceTurn()`/teardown, and there's nothing to react
    /// to while idle anyway (`endActiveSessionForInterruption` checks `state`
    /// and no-ops if there's nothing active).
    ///
    /// LiveKit's own `AudioSessionEngineObserver` (confirmed via the
    /// installed SDK source) configures the session category/mode/Bluetooth
    /// routing automatically whenever the room's audio engine is enabled,
    /// and WebRTC's underlying audio unit has its own internal interruption
    /// handling to keep itself consistent at the hardware level -- but
    /// neither surfaces an app-observable signal that anything happened
    /// (`RoomDelegate` has no interruption/route-change callback). Without
    /// this, a phone call or headphone disconnect mid-turn would leave the
    /// UI stuck showing "Listening…"/"Thinking…" indefinitely with no
    /// explanation -- exactly the silent-hang failure mode Milestone 9.1
    /// closed for pipeline errors, just for a different trigger.
    private func observeAudioSessionEvents() {
        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { [weak self] notification in
            guard
                let typeValue = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                AVAudioSession.InterruptionType(rawValue: typeValue) == .began
            else { return }
            Task { @MainActor in
                self?.endActiveSessionForInterruption(
                    reason: "Your voice session was interrupted (e.g. a phone call). Tap the mic to start again."
                )
            }
        }
        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
        ) { [weak self] notification in
            // .oldDeviceUnavailable specifically -- e.g. AirPods disconnecting
            // mid-conversation, falling back to the speaker. Other reasons
            // (a new device becoming available, category/override changes
            // LiveKit itself makes) aren't disruptive enough to end the turn
            // over, and reacting to every route change would be noisy for no
            // benefit.
            guard
                let reasonValue = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                AVAudioSession.RouteChangeReason(rawValue: reasonValue) == .oldDeviceUnavailable
            else { return }
            Task { @MainActor in
                self?.endActiveSessionForInterruption(
                    reason: "Audio output changed (e.g. headphones disconnected). Tap the mic to start again."
                )
            }
        }
        // LiveKit connection-minute cost control, and the reason
        // `listeningIdleDeadline` alone isn't enough: every local watchdog
        // in this file is a `Timer` or a `Task.sleep`, and both stop
        // counting the moment the app loses foreground execution -- so a
        // user who swipes away mid-turn leaves the room connected (and
        // billed) until LiveKit's own server-side reaper eventually notices
        // the socket is gone. That was already confirmed live from the other
        // direction, in `finishVoiceSessionAfterReply`'s own history: a room
        // sat open and untouched for two and a half minutes after a reply
        // finished, consistent with exactly this. Backgrounding is the last
        // moment code still reliably runs, so the session is ended here
        // instead. Deliberately no matching `willEnterForeground` resume,
        // for the same reason `endActiveSessionForInterruption` never
        // resumes anything: silently reopening a mic session the user didn't
        // ask for is the surprise-recording failure mode, not a convenience.
        didEnterBackgroundObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main
        ) { [weak self] _ in
            // Same main-actor hop as the two observers above: `queue: .main`
            // gets this onto the main thread, but the closure is still
            // non-isolated as far as the compiler is concerned, so it can't
            // touch this `@MainActor` class's state without the hop.
            Task { @MainActor in
                self?.endActiveSessionForInterruption(
                    reason: "Voice session ended because Atlas went to the background. Tap the mic to start again."
                )
            }
        }
    }

    /// Ends whatever's currently active -- a live voice turn, a reply still
    /// being awaited, or local replay playback -- with a clear explanation,
    /// rather than leaving any of them stuck. Deliberately does not attempt
    /// to resume automatically once the interruption ends: per the spec's
    /// own mic-launch-guard reasoning (an unmistakable indicator + instant
    /// cancel, never a surprise recording), silently resuming a mic-capture
    /// session after e.g. a phone call ends would be the same class of
    /// surprise the guard exists to prevent -- ending cleanly and letting
    /// the user deliberately tap the mic again is the safer default.
    private func endActiveSessionForInterruption(reason: String) {
        guard !isEndingSessionForInterruption else { return }
        if playbackEngine != nil {
            stopLocalPlayback()
            onPlaybackStopped?()
        }
        switch state {
        case .listening, .connecting:
            // isEndingSessionForInterruption (not `state` itself --
            // `cancelVoiceTurn()`'s own entry guard requires seeing
            // `.listening`/`.connecting` unchanged when it runs, so this
            // can't reuse the `.stopping` transitional-state trick
            // `cancelVoiceTurn()` uses internally) guards against a
            // near-simultaneous second notification (e.g. a real phone call
            // can plausibly fire both `interruptionNotification` and a
            // `.oldDeviceUnavailable` route change in quick succession)
            // also matching this branch and surfacing a second, redundant
            // error message before the first `cancelVoiceTurn()` call has
            // actually run.
            isEndingSessionForInterruption = true
            onError?(reason)
            Task {
                await self.cancelVoiceTurn()
                // Same gap as finishVoiceSessionAfterReply's failure paths
                // (see onReplyFailed's doc comment), different call site:
                // cancelVoiceTurn() here never tells a caller the session
                // died, so without this a caller left showing "Listening…"
                // stays stuck showing it -- self-correcting only if the user
                // happens to tap the (now-dead) mic button and notices.
                self.onReplyFailed?()
                self.isEndingSessionForInterruption = false
            }
        case .awaitingReply:
            isEndingSessionForInterruption = true
            onError?(reason)
            Task {
                await self.finishVoiceSessionAfterReply()
                self.isEndingSessionForInterruption = false
            }
        case .idle, .stopping, .stopped:
            break
        }
    }

    // MARK: - Data-channel heartbeat (dead-channel detection)

    /// Starts pinging once a room connection exists; called from
    /// `startVoiceTurn()` right after `room.connect()` succeeds.
    private func startHeartbeat() {
        heartbeatTask?.cancel()
        lastPongReceivedAt = Date()
        heartbeatTask = Task { [weak self] in
            while true {
                try? await Task.sleep(nanoseconds: UInt64((self?.heartbeatInterval ?? 5) * 1_000_000_000))
                guard let self, !Task.isCancelled, self.room != nil else { return }
                let sinceLastPong = Date().timeIntervalSince(self.lastPongReceivedAt ?? Date())
                if sinceLastPong > self.heartbeatTimeout {
                    self.handleDeadDataChannel()
                    return
                }
                await self.publish(VoiceDataMessage(type: "ping", text: nil))
            }
        }
    }

    /// Called from `teardownRoom()` -- no point pinging a room that's
    /// already gone or on its way out.
    private func stopHeartbeat() {
        heartbeatTask?.cancel()
        heartbeatTask = nil
        lastPongReceivedAt = nil
    }

    /// The actual recovery action once a missed pong confirms the data
    /// channel is dead: reuses `endActiveSessionForInterruption`'s existing
    /// state handling wholesale (it already correctly covers both
    /// `.listening`/`.connecting` and `.awaitingReply`, and its
    /// `isEndingSessionForInterruption` guard already prevents double-
    /// handling if a real interruption fires around the same time) rather
    /// than duplicating that logic for a third trigger. Deliberately does
    /// NOT try to resume automatically for the same reason that method
    /// doesn't either -- a channel just proven dead is not something to
    /// silently retry into.
    private func handleDeadDataChannel() {
        stopHeartbeat()
        endActiveSessionForInterruption(
            reason: "Lost connection to Atlas. Tap the mic to try again."
        )
    }

    // MARK: - Session lifecycle

    /// Starts a voice turn: fetches a token, connects to the room, publishes
    /// a one-time `context_seed` message carrying recent text-chat history
    /// (Milestone 7.5, mode continuity/FR9 -- see `publishContextSeed(from:)`),
    /// and begins on-device transcription. Returns `true` if listening
    /// started successfully; `false` if it failed (`onError` has already
    /// fired with a user-facing message).
    ///
    /// `priorMessages` is `ChatViewModel.messages` as of the moment the user
    /// tapped the mic -- the same array text chat's `/chat` calls already
    /// resend in full on every turn, so this closes the other direction of
    /// mode continuity (voice->text already worked, since voice-obtained
    /// turns get appended to that same shared array).
    @discardableResult
    func startVoiceTurn(accessToken: String?, priorMessages: [ChatMessage], location: String? = nil) async -> Bool {
        guard state == .idle || state == .stopped else { return state == .listening || state == .connecting }
        isCancelled = false
        state = .connecting

        // Fresh per-turn bookkeeping -- a new `Room` is created below, so
        // nothing from any previous turn should carry over. `drainBuffers()`
        // is belt-and-suspenders: by the time a previous turn's room was
        // actually torn down, its last `finalizeCurrentReplyBuffer()` call
        // should already have drained `replayRenderer` empty, but this
        // guarantees it regardless of exactly which path finished that turn
        // (normal completion, cancel, or the `awaitingReplyTimeout` safety
        // net).
        lastReplyBuffers.removeAll()
        _ = replayRenderer.drainBuffers()
        replyTextReceived = false
        replyAudioFinished = false

        guard let accessToken else {
            onError?("Your session expired. Please sign in again.")
            state = .idle
            return false
        }

        do {
            let voiceToken = try await fetchVoiceToken(accessToken: accessToken)

            let room = Room()
            room.delegates.add(delegate: self)
            self.room = room
            try await room.connect(url: voiceToken.url, token: voiceToken.token)
            startHeartbeat()

            // Before any real utterance can possibly be published, seed the
            // backend's fresh voice-session LLM context with recent
            // text-chat history -- see publishContextSeed(from:).
            await publishContextSeed(from: priorMessages)
            if let location {
                await publishLocation(location)
            }

            try await transcriptionService.start()

            eventTask = Task { [weak self] in
                guard let self else { return }
                for await event in self.transcriptionService.events {
                    await self.handle(event)
                }
            }

            state = .listening
            // Cost control: from here on, an unattended mic can only hold
            // the room open for `listeningIdleTimeout` -- see
            // `listeningIdleDeadline`.
            listeningIdleDeadline.start()
            return true
        } catch let error as VoiceTranscriptionError {
            onError?(error.errorDescription ?? "Could not start a voice session.")
            await tearDownAfterFailure()
            return false
        } catch {
            onError?("Could not start a voice session: \(error.localizedDescription)")
            await tearDownAfterFailure()
            return false
        }
    }

    /// Normal completion: stops transcription and waits for `eventTask` to
    /// drain (so a trailing `.final`/`.speechStopped` from an open turn still
    /// gets forwarded and published), but -- Finding 0 -- deliberately does
    /// NOT disconnect from the room here. The backend's reply can only
    /// arrive over this same room connection and takes real time to
    /// generate, so the room is left connected (and the `RoomDelegate`
    /// active) in `.awaitingReply` until the reply genuinely finishes (see
    /// `finishReplyIfComplete()`) or `cancelVoiceTurn()` is called.
    func stopVoiceTurn() async {
        guard state == .listening || state == .connecting else { return }
        // Finding 3: flip state synchronously, before the first `await`,
        // so a near-simultaneous second call (rapid double-tap) sees
        // `.stopping` and returns early via the guard above instead of
        // both calls racing into the drain/teardown logic concurrently.
        state = .stopping

        await transcriptionService.stop()
        await eventTask?.value
        eventTask = nil

        state = .awaitingReply
        awaitingReplyDeadline.start()
        // Rare-but-possible race: the reply could already have fully
        // arrived (both `replyTextReceived`/`replyAudioFinished` true)
        // while this method was still awaiting the transcription
        // drain above -- `finishReplyIfComplete()`'s own `state ==
        // .awaitingReply` guard skips it in that window, since state was
        // still `.stopping` at the time. Check again now that state has
        // just become `.awaitingReply`, so that race doesn't fall all the
        // way back to the `awaitingReplyTimeout` safety net.
        finishReplyIfComplete()
    }

    /// Instant cancel: stops transcription too, but marks the turn cancelled
    /// first so `handle(_:)` skips forwarding/publishing anything still
    /// draining out of `transcriptionService` -- nothing captured so far is
    /// sent to the backend or surfaced as a chat message. Unlike
    /// `stopVoiceTurn()`, this DOES tear the room down immediately: the user
    /// explicitly bailed out, so there is no reply to wait for.
    func cancelVoiceTurn() async {
        guard state == .listening || state == .connecting else { return }
        isCancelled = true
        // Finding 3: same synchronous transitional-state guard as
        // `stopVoiceTurn()` above, and using the SAME `.stopping` value
        // means a `stopVoiceTurn()`/`cancelVoiceTurn()` race (not just two
        // calls to the same method) is also closed -- whichever wins the
        // guard flips state away from `.listening`/`.connecting` first, and
        // the other sees `.stopping` and backs off.
        state = .stopping

        await transcriptionService.stop()
        await eventTask?.value
        eventTask = nil

        await teardownRoom()
        state = .stopped
        isCancelled = false
    }

    /// Ends whatever voice session is in progress, from ANY state, because
    /// the object graph that owns this controller is about to be released
    /// (sign-out -- see `ActiveVoiceSession`). The existing exits each only
    /// cover part of the state machine: `cancelVoiceTurn()` requires
    /// `.listening`/`.connecting` and `finishVoiceSessionAfterReply()`
    /// requires `.awaitingReply`, so calling either one alone would silently
    /// no-op for a session sitting in the other half and leave the room
    /// connected and the mic capturing with nobody left to stop them.
    func endSessionForSignOut() async {
        switch state {
        case .listening, .connecting:
            await cancelVoiceTurn()
        case .awaitingReply:
            // Deliberately the failure-flavoured finish (the default
            // `replyCompletedSuccessfully: false`). The success flavour
            // fires `onReplyCompleted`, which is what continuous-conversation
            // mode uses to auto-start ANOTHER voice turn -- the last thing
            // anyone wants from the sign-out path. `onReplyFailed` instead
            // just resets the caller's UI state, which is all that's wanted
            // here.
            await finishVoiceSessionAfterReply()
        case .idle, .stopping, .stopped:
            // Nothing connected (`.idle`/`.stopped`), or an in-flight
            // stop/cancel is already tearing the same room down
            // (`.stopping`) and racing it would just have both paths
            // calling `teardownRoom()` at once.
            break
        }
        // Separate from the room teardown above on purpose: a reply's audio
        // can still be playing (live track volume, or a local replay through
        // `AVAudioEngine`) in states where there is nothing left to
        // disconnect, and audio outliving the screen that explains it is its
        // own bug.
        stopPlayback()
    }

    /// The single chokepoint that actually disconnects from the room --
    /// called from `cancelVoiceTurn()` (explicit bail-out) and
    /// `finishVoiceSessionAfterReply()` (the reply genuinely completed, or
    /// the `awaitingReplyTimeout` safety net fired). Also cancels any
    /// pending reply-completion bookkeeping and both session deadlines, so
    /// nothing fires after the room is gone.
    private func teardownRoom() async {
        awaitingReplyDeadline.cancel()
        listeningIdleDeadline.cancel()
        pendingTeardownTask?.cancel()
        pendingTeardownTask = nil
        stopHeartbeat()

        room?.delegates.remove(delegate: self)
        if let currentAudioTrack {
            currentAudioTrack.remove(audioRenderer: replayRenderer)
        }
        currentAudioTrack = nil
        stopQuietCheckTimer()
        await room?.disconnect()
        room = nil
    }

    // MARK: - Reply completion / deferred room teardown (Finding 0)

    /// Called after both `replyTextReceived` and `replyAudioFinished` become
    /// `true` (in either order -- see the call sites in `handleIncomingData`
    /// and `checkForQuiet`). Schedules the actual teardown after
    /// `roomTeardownGraceWindow`, rather than immediately, so a false-alarm
    /// "gone quiet" (a natural mid-sentence pause) has a chance to be
    /// cancelled by resumed audio before the room is actually severed.
    private func finishReplyIfComplete() {
        guard state == .awaitingReply else { return }
        guard replyTextReceived, replyAudioFinished else { return }

        pendingTeardownTask?.cancel()
        pendingTeardownTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64((self?.roomTeardownGraceWindow ?? 1.5) * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            // The only call site that represents a genuinely complete,
            // error-free reply -- see onReplyCompleted's doc comment.
            await self.finishVoiceSessionAfterReply(replyCompletedSuccessfully: true)
        }
    }

    /// Actually ends the voice session: tears down the room. Guarded on
    /// `state == .awaitingReply` so this is a no-op if `cancelVoiceTurn()`
    /// (or a second competing finish path) already tore the room down first.
    ///
    /// `replyCompletedSuccessfully` defaults to `false` -- only
    /// `finishReplyIfComplete()`'s call site (genuine completion) passes
    /// `true`. The other three callers (the 30s `awaitingReplyTimeout`
    /// safety net, a `pipeline_error` message, and an interruption while
    /// `.awaitingReply`) are all failure/abort paths and fire `onReplyFailed`
    /// instead.
    ///
    /// Found live: a caller (`ChatViewModel`) only ever heard about a
    /// SUCCESSFUL end of turn (`onReplyCompleted`) -- there was no signal
    /// at all for the failure paths, so its own UI state (`voiceState`)
    /// stayed stuck at `.thinking`/`.speaking` forever once one of them
    /// fired, permanently disabling the mic button (it's disabled in
    /// exactly those two states) with no way to recover short of
    /// relaunching the app. Confirmed via a live backend log: a reply
    /// finished normally, then the room sat open and untouched for two and
    /// a half minutes -- consistent with the app losing foreground
    /// execution (RunLoop timers/Task.sleep both pause while backgrounded)
    /// partway through `checkForQuiet()`'s poll, before it ever reached
    /// genuine-quiet detection -- until LiveKit's own connection eventually
    /// gave up and disconnected the participant server-side, landing here
    /// via the interruption/timeout path with nothing telling `ChatViewModel`
    /// the turn was over.
    private func finishVoiceSessionAfterReply(replyCompletedSuccessfully: Bool = false) async {
        guard state == .awaitingReply else { return }
        // Belt-and-suspenders: capture anything still sitting in the
        // renderer's buffer (e.g. the `awaitingReplyTimeout` safety net
        // fired without a `checkForQuiet()` ever having run) so
        // `replayLastReply()` has the fullest possible reply audio even in
        // that edge case.
        finalizeCurrentReplyBuffer()
        await teardownRoom()
        state = .stopped
        if replyCompletedSuccessfully {
            onReplyCompleted?()
        } else {
            onReplyFailed?()
        }
    }

    private func tearDownAfterFailure() async {
        eventTask?.cancel()
        eventTask = nil
        await transcriptionService.stop()
        await teardownRoom()
        state = .idle
    }

    // MARK: - Playback control

    /// Hard-stops whatever's currently audible: mutes the live subscribed
    /// remote audio track (if the reply is still streaming in) and stops any
    /// local replay playback (if a buffered reply is being replayed).
    func stopPlayback() {
        currentAudioTrack?.volume = 0
        stopLocalPlayback()
        stopQuietCheckTimer()
        onPlaybackStopped?()
    }

    /// Plays the most recently completed reply's buffered PCM back through a
    /// local `AVAudioEngine`, independent of the live LiveKit track.
    func replayLastReply() {
        guard let format = lastReplyBuffers.first?.format, !lastReplyBuffers.isEmpty else { return }

        // Milestone 9.2 review finding: Replay is reachable from the
        // `.speaking` UI state, which begins the moment the LIVE reply
        // starts streaming in -- not only after it finishes -- so the live
        // LiveKit track can still be audible when this runs. Mute it first
        // (mirroring stopPlayback()'s own handling of the same track)
        // before reassigning the shared AVAudioSession and starting a
        // second, local playback engine, so the two don't overlap/contend.
        currentAudioTrack?.volume = 0

        stopLocalPlayback()

        // Milestone 9.2 (§11), corrected after code review: the ORIGINAL
        // version of this fix assumed replay always runs after the room has
        // disconnected, but Replay is reachable from the `.speaking` UI
        // state, which begins on the very first LIVE reply frame -- `room`
        // can still be non-nil and LiveKit's own AudioSessionEngineObserver
        // can still own an active `.playAndRecord` session at this point.
        // Reassigning the category out from under it here would fight
        // LiveKit's own session ownership and could glitch the live track.
        // Only reconfigure when the room is genuinely gone (`room == nil`,
        // i.e. LiveKit no longer needs the session for anything) -- that's
        // the only situation where the session could have been deactivated/
        // reset to a silent-switch-obeying default in the first place. If
        // the room is still connected, LiveKit's already-active
        // `.playAndRecord` session plays a second local AVAudioEngine's
        // output through it just fine with no reconfiguration needed.
        if room == nil {
            do {
                try AVAudioSession.sharedInstance().setCategory(.playback)
                try AVAudioSession.sharedInstance().setActive(true)
            } catch {
                onError?("Could not replay the last reply: \(error.localizedDescription)")
                return
            }
        }

        let engine = AVAudioEngine()
        let player = AVAudioPlayerNode()
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)

        do {
            try engine.start()
        } catch {
            onError?("Could not replay the last reply: \(error.localizedDescription)")
            return
        }

        playbackEngine = engine
        playbackPlayerNode = player

        let buffers = lastReplyBuffers
        for (index, buffer) in buffers.enumerated() {
            let isLast = index == buffers.count - 1
            player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                guard isLast else { return }
                Task { @MainActor in self?.finishLocalPlayback() }
            }
        }
        player.play()
        onPlaybackStarted?()
    }

    private func stopLocalPlayback() {
        guard playbackEngine != nil else { return }
        playbackPlayerNode?.stop()
        playbackEngine?.stop()
        playbackPlayerNode = nil
        playbackEngine = nil
        // Symmetric with replayLastReply()'s own room == nil guard: only
        // deactivate if the room is genuinely gone. If it's still connected,
        // this playback never activated the session itself (replayLastReply
        // skipped that when the room was present), and deactivating it here
        // would instead pull the session out from under LiveKit's own still-
        // live track.
        if room == nil {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
    }

    private func finishLocalPlayback() {
        stopLocalPlayback()
        onPlaybackStopped?()
    }

    // MARK: - Remote audio frame bookkeeping

    /// Called (via `replayRenderer.onFrame`, already hopped to the main
    /// actor) every time a PCM frame arrives from the subscribed reply
    /// track, with that frame's peak amplitude. Restarts the "gone quiet"
    /// debounce window and, on the first MEANINGFUL frame since a quiet
    /// period, fires `onPlaybackStarted`.
    ///
    /// `peakAmplitude` is what makes this correct at all: confirmed live via
    /// temporary diagnostic logging that frames arrive continuously, roughly
    /// every 10ms, for as long as the track is subscribed -- including
    /// during genuine silence (WebRTC's own comfort-noise/keep-alive
    /// packets, not a bug). Treating every arrival as "still talking" (the
    /// original design here) meant `lastAudioFrameTime` could never age past
    /// `playbackQuietWindow`, so `checkForQuiet()` could never detect real
    /// quiet at all -- the room would sit in `.awaitingReply` until
    /// `awaitingReplyTimeout`'s hard ceiling eventually forced it closed,
    /// not the ~2.5s this mechanism is supposed to take. Frames below
    /// `replySilenceThreshold` still arrive and are still logged/tapped
    /// (`ReplayAudioRenderer.render` never filters anything -- replay/buffer
    /// capture needs the real trailing audio, not just the "loud enough"
    /// parts) but no longer count as a sign the reply is ongoing.
    ///
    /// Note this does NOT clear `lastReplyBuffers` on `wasQuiet` (unlike an
    /// earlier draft of Finding 2's fix) -- under Finding 0's redesign a
    /// single room connection spans exactly one reply, so a `wasQuiet`
    /// transition here almost always means `checkForQuiet()`'s 1.0s
    /// heuristic fired on an ordinary mid-sentence pause and audio for the
    /// SAME reply has now resumed (Finding 4), not that a genuinely new
    /// reply has started. Clearing here would discard everything
    /// accumulated earlier in that same reply. `lastReplyBuffers` is instead
    /// cleared exactly once per real new turn, in `startVoiceTurn()`.
    private func audioFrameReceived(peakAmplitude: Float) {
        // Must run on every arrival, not just meaningful ones -- the poll
        // needs to already be ticking by the time real speech starts (and
        // stops), not only once the first above-threshold frame shows up.
        startQuietCheckTimerIfNeeded()

        guard peakAmplitude >= replySilenceThreshold else { return }

        let now = Date()
        let wasQuiet = lastAudioFrameTime == nil
        lastAudioFrameTime = now
        if wasQuiet {
            // Audio resumed after a quiet gap -- the earlier "gone quiet"
            // was a false alarm as far as the reply being *done* goes.
            // Cancel any teardown that was pending because of it.
            replyAudioFinished = false
            pendingTeardownTask?.cancel()
            pendingTeardownTask = nil
            onPlaybackStarted?()
        }
    }

    private func startQuietCheckTimerIfNeeded() {
        guard quietCheckTimer == nil else { return }
        let timer = Timer(timeInterval: 0.3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.checkForQuiet() }
        }
        RunLoop.main.add(timer, forMode: .common)
        quietCheckTimer = timer
    }

    private func stopQuietCheckTimer() {
        quietCheckTimer?.invalidate()
        quietCheckTimer = nil
        lastAudioFrameTime = nil
    }

    private func checkForQuiet() {
        guard let lastAudioFrameTime else { return }
        let sinceLastFrame = Date().timeIntervalSince(lastAudioFrameTime)
        guard sinceLastFrame >= playbackQuietWindow else {
            // Still actively receiving audio -- this tick is proof the
            // reply hasn't stalled, so push the reply watchdog back out.
            // Piggybacks on this timer's existing 0.3s cadence rather than
            // resetting on every single PCM frame (far higher frequency
            // than needed for a coarse safety net). awaitingReplyDeadline
            // itself owns the hard-ceiling bookkeeping (never past
            // maxAwaitingReplyDuration) -- this call site doesn't need to
            // know that ceiling exists, only that "still receiving audio"
            // is a sign of life worth reporting.
            if state == .awaitingReply {
                awaitingReplyDeadline.extend()
            }
            return
        }
        stopQuietCheckTimer()
        finalizeCurrentReplyBuffer()
        onPlaybackStopped?()
        replyAudioFinished = true
        finishReplyIfComplete()
    }

    /// Moves whatever's been captured for the in-progress reply into
    /// `lastReplyBuffers` (for `replayLastReply()`). Accumulates rather than
    /// replaces (Finding 2): this is called twice per reply in the normal
    /// case -- once when `assistant_reply` text arrives (draining whatever's
    /// buffered so far) and again when the quiet-timer heuristic fires for
    /// the trailing audio tail. An earlier version of this method assigned
    /// (`lastReplyBuffers = drained`), so the second call silently
    /// overwrote/discarded the first call's audio, leaving
    /// `replayLastReply()` with only a short fragment. `lastReplyBuffers` is
    /// cleared exactly once per new turn, in `startVoiceTurn()` -- NOT here
    /// and NOT in `audioFrameReceived()` (see that method's doc comment for
    /// why draining-without-clearing-on-resume matters for Finding 4).
    private func finalizeCurrentReplyBuffer() {
        let drained = replayRenderer.drainBuffers()
        guard !drained.isEmpty else { return }
        lastReplyBuffers.append(contentsOf: drained)
    }

    // MARK: - Local transcription event handling

    private func handle(_ event: VoiceTranscriptEvent) async {
        guard !isCancelled else { return }
        switch event {
        case .speechStarted:
            // Someone is actually here and talking, so the idle-listening
            // deadline has done its job for this turn: the rest of it is
            // already bounded by the `speech_stopped` turn-boundary handling
            // below plus `maxAwaitingReplyDuration`. Cancelling (rather than
            // extending) is what keeps a real, slow-spoken utterance from
            // ever being cut off mid-sentence.
            listeningIdleDeadline.cancel()
            onSpeechStarted?()
            await publish(VoiceDataMessage(type: "speech_started", text: nil))
        case .interim(let text):
            onInterimTranscript?(text)
            await publish(VoiceDataMessage(type: "interim", text: text))
        case .final(let text):
            onFinalTranscript?(text)
            await publish(VoiceDataMessage(type: "final", text: text))
        case .speechStopped:
            onSpeechStopped?()
            await publish(VoiceDataMessage(type: "speech_stopped", text: nil))
            // Real-device acoustic-feedback bug, confirmed via a live
            // conversation log where the "user" appeared to repeat the
            // assistant's own reply back verbatim, word for word: this
            // SAME speech_stopped signal is what the backend's
            // ExternalUserTurnStrategies uses to decide the user's turn
            // has ended and start generating + playing a reply -- but
            // until now, nothing on this end stopped the LOCAL mic engine
            // in response to it, since that was solely tap-to-stop's job
            // (Milestone 7.4b, a deliberate UX choice, not an oversight).
            // With the mic still actively recording through the phone's
            // own speaker (no headset, no acoustic echo cancellation
            // between the two), it picks up the reply itself and feeds it
            // back in as if the user said it -- confirmed capable of
            // cascading indefinitely, not just a one-off glitch. Stopping
            // here does not remove tap-to-stop -- a manual tap racing this
            // is safe, stopVoiceTurn()'s own state guard makes the second
            // caller a no-op (Finding 3, Milestone 7.4b) -- it just means
            // the mic ALSO stops automatically once a turn is detected as
            // complete, matching how every other voice assistant behaves
            // and closing a real conversation-corrupting bug, not just an
            // inconvenience.
            //
            // Dispatched as a new Task rather than awaited inline: this
            // method runs as part of eventTask's own `for await` loop, and
            // stopVoiceTurn() awaits `eventTask?.value` -- calling it
            // in-line here would be the task awaiting its own completion,
            // a deadlock.
            if state == .listening {
                Task { await self.stopVoiceTurn() }
            }
        }
    }

    /// Milestone 7.5 (mode continuity, FR9): publishes a one-time
    /// `context_seed` data message carrying recent text-chat history, so the
    /// backend's voice-session LLM context (seeded empty at pipeline
    /// startup, see `app/voice_agent.py`) doesn't start from scratch when a
    /// user switches from typing to voice mid-conversation. Filters
    /// `priorMessages` to only `.user`/`.assistant` entries with non-nil,
    /// non-empty (after trimming) content -- never `.system`/`.tool`,
    /// matching the backend bridge's own role allowlist in
    /// `app/voice_transcript_bridge.py` -- takes the most recent 20, and
    /// publishes them via the same `room.localParticipant.publish(data:
    /// options:)` mechanism `publish(_:)` uses for the other outgoing
    /// message types. If nothing survives filtering (a fresh conversation
    /// with no history yet), publishes nothing.
    private func publishContextSeed(from priorMessages: [ChatMessage]) async {
        let seedEntries: [ContextSeedEntry] = priorMessages.compactMap { message in
            guard message.role == .user || message.role == .assistant else { return nil }
            guard let content = message.content?.trimmingCharacters(in: .whitespacesAndNewlines), !content.isEmpty
            else {
                return nil
            }
            return ContextSeedEntry(role: message.role.rawValue, content: content)
        }
        guard !seedEntries.isEmpty else { return }

        let recent = Array(seedEntries.suffix(20))
        guard let room else { return }
        do {
            let data = try JSONEncoder().encode(ContextSeedMessage(type: "context_seed", messages: recent))
            try await room.localParticipant.publish(data: data, options: DataPublishOptions(reliable: true))
        } catch {
            onError?("Failed to send conversation context to Atlas: \(error.localizedDescription)")
        }
    }

    /// One-time `location` data message (same publish mechanism/timing as
    /// `publishContextSeed`, sent right after connecting): the voice
    /// pipeline's system prompt is built once at `voice_agent.py` process
    /// startup with no location context at all, unlike text chat's `/chat`
    /// requests, which get a fresh `user_location` field every call. This
    /// is how a live voice session gets that same context.
    private func publishLocation(_ location: String) async {
        guard let room else { return }
        do {
            let data = try JSONEncoder().encode(LocationSeedMessage(type: "location", location: location))
            try await room.localParticipant.publish(data: data, options: DataPublishOptions(reliable: true))
        } catch {
            onError?("Failed to send your location to Atlas: \(error.localizedDescription)")
        }
    }

    private func publish(_ message: VoiceDataMessage) async {
        guard let room else { return }
        do {
            let data = try JSONEncoder().encode(message)
            try await room.localParticipant.publish(data: data, options: DataPublishOptions(reliable: true))
        } catch {
            onError?("Failed to send transcript to Atlas: \(error.localizedDescription)")
        }
    }

    private func fetchVoiceToken(accessToken: String) async throws -> VoiceTokenResponse {
        // Found by review: this request carried no timezone at all, so the
        // backend fell back to a hardcoded developer timezone
        // (`VOICE_DEV_TIMEZONE`) for every real user -- every voice-mode
        // date resolution ("tomorrow at 9") and every `set_reminder`
        // "has that time already passed?" check was computed in someone
        // else's day. The text path never had this bug: `ChatViewModel.send`
        // has always sent `TimeZone.current.identifier`, so this just brings
        // voice in line with it.
        //
        // Sent as a URL query parameter because `/voice/token` takes no
        // request body, and built through `URLComponents` rather than
        // interpolated into the string: IANA identifiers contain "/"
        // ("America/Los_Angeles"), and a few contain "+"
        // ("Etc/GMT+8") -- characters that must be percent-encoded inside a
        // query value and that string interpolation would pass through raw.
        // The backend treats the parameter as optional and keeps its old
        // default when it's absent, so this stays backward compatible in
        // both directions (old client/new server, new client/old server).
        guard
            var components = URLComponents(string: "\(baseURL)/voice/token")
        else {
            throw VoiceSessionError.tokenFetchFailed("Couldn't build the voice session request.")
        }
        components.queryItems = [
            URLQueryItem(name: "timezone", value: TimeZone.current.identifier)
        ]
        guard let url = components.url else {
            throw VoiceSessionError.tokenFetchFailed("Couldn't build the voice session request.")
        }

        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: urlRequest)
        guard let http = response as? HTTPURLResponse else {
            throw VoiceSessionError.tokenFetchFailed("No response from server.")
        }
        guard http.statusCode == 200 else {
            let message: String
            switch http.statusCode {
            case 401:
                message = "Your session expired. Please sign in again."
            case 429:
                // Milestone 9.3: mirrors ChatViewModel's identical handling
                // -- surface the backend's specific detail (burst vs. daily
                // cap) rather than a generic message.
                message = Self.decodeErrorDetail(from: data)
                    ?? "Too many voice sessions started -- please wait a moment and try again."
            default:
                message = "Failed to start a voice session (server returned \(http.statusCode))."
            }
            throw VoiceSessionError.tokenFetchFailed(message)
        }
        return try Self.responseDecoder.decode(VoiceTokenResponse.self, from: data)
    }

    // MARK: - Incoming voice message decoding

    /// Finding 1 (security review, HIGH): only acts on `assistant_reply`/
    /// `tool_result` messages sent by the backend's known voice-agent
    /// identity (`expectedAgentIdentity`). Without this, any other
    /// participant able to join the shared dev room could forge a
    /// `tool_result` for `set_reminder` (which triggers a real on-device
    /// local notification via `ChatViewModel`) or inject fake assistant text
    /// straight into the chat transcript. A mismatch is not silently
    /// swallowed -- `onError` fires so a stale/second agent process (or a
    /// genuine spoof attempt) is at least observable, rather than either
    /// acted on or invisible.
    private func handleIncomingData(_ data: Data, from participant: RemoteParticipant?) {
        guard let envelope = try? Self.responseDecoder.decode(VoiceIncomingEnvelope.self, from: data) else { return }
        guard participant?.identity?.stringValue == expectedAgentIdentity else {
            onError?("Ignored a voice message from an unexpected participant.")
            return
        }
        switch envelope.type {
        case "assistant_reply":
            guard let message = try? Self.responseDecoder.decode(AssistantReplyMessage.self, from: data) else { return }
            // The text is the authoritative "this reply is complete" signal
            // per spec, even if a trailing audio tail is still draining in
            // via the renderer -- finalize now so replay always has
            // *something* even if the quiet-timer heuristic is still
            // running.
            finalizeCurrentReplyBuffer()
            onAssistantReply?(message.text)
            replyTextReceived = true
            finishReplyIfComplete()
        case "tool_result":
            guard let message = try? Self.responseDecoder.decode(ToolResultMessage.self, from: data) else { return }
            onToolResult?(message)
        case "pong":
            // Dead-data-channel probe response -- see heartbeatTask's doc
            // comment. No payload beyond `type` to decode.
            lastPongReceivedAt = Date()
        case "pipeline_error":
            guard let message = try? Self.responseDecoder.decode(PipelineErrorMessage.self, from: data) else { return }
            // Guarded on .awaitingReply, mirroring awaitingReplyDeadline's own
            // onExpired guard: a message delivered late (after a local timeout
            // already surfaced its own error and started tearing the room
            // down) must not overwrite errorMessage with a second, redundant
            // one -- errorMessage is a plain last-write-wins property, not
            // scoped per-turn.
            guard state == .awaitingReply else { return }
            // No reply is coming for this turn -- surface it immediately
            // rather than leaving the user staring at "thinking…" for the
            // full 30s `awaitingReplyTimeout` safety net. That net still
            // fires and ends the turn if this message never arrives at all
            // (e.g. the backend process itself is down).
            onError?(message.message)
            Task { await self.finishVoiceSessionAfterReply() }
        default:
            break
        }
    }
}

// MARK: - RoomDelegate

extension VoiceSessionController: RoomDelegate {
    /// Real signature confirmed from the installed SDK source
    /// (`Sources/LiveKit/Protocols/RoomDelegate.swift`): this brief's
    /// original doc-derived guess omitted the trailing `encryptionType`
    /// parameter, which the currently-installed SDK version requires (the
    /// 4-parameter overload without it exists only as an
    /// `@available(*, unavailable, renamed:)` shim).
    nonisolated func room(
        _ room: Room, participant: RemoteParticipant?, didReceiveData data: Data, forTopic topic: String,
        encryptionType: EncryptionType
    ) {
        Task { @MainActor in self.handleIncomingData(data, from: participant) }
    }

    nonisolated func room(_ room: Room, participant: RemoteParticipant, didSubscribeTrack publication: RemoteTrackPublication) {
        guard publication.kind == .audio, let audioTrack = publication.track as? RemoteAudioTrack else { return }
        Task { @MainActor in
            self.currentAudioTrack = audioTrack
            audioTrack.volume = 1
            audioTrack.add(audioRenderer: self.replayRenderer)
        }
    }

    nonisolated func room(_ room: Room, participant: RemoteParticipant, didUnsubscribeTrack publication: RemoteTrackPublication) {
        guard publication.kind == .audio, let audioTrack = publication.track as? RemoteAudioTrack else { return }
        Task { @MainActor in
            audioTrack.remove(audioRenderer: self.replayRenderer)
            if self.currentAudioTrack === audioTrack {
                self.currentAudioTrack = nil
            }
        }
    }
}

// MARK: - Extendable deadline (reply-watchdog refactor)

/// A one-shot deadline that fires `onExpired` after `duration` seconds
/// unless `extend()` keeps pushing it back out -- but never past
/// `maxTotalDuration` measured from `start()`, however many times
/// `extend()` is called. This is the general shape `awaitingReplyTimeout`/
/// `maxAwaitingReplyDuration` always needed (tolerate an arbitrarily long
/// reply, but never hang forever if whatever's supposed to call `extend()`
/// gets stuck) -- pulled into its own `@MainActor` type (not detached from
/// any actor: the only caller, `VoiceSessionController`, is itself
/// `@MainActor`, and `onExpired` needs to touch its isolated state) so that
/// caller no longer has to hand-roll the reset-with-a-cap bookkeeping
/// inline. Extracted specifically because `checkForQuiet()` -- whose only
/// real job is deciding whether reply audio has gone quiet -- used to
/// reach directly into this timeout's own scheduling/elapsed-time
/// bookkeeping to keep it alive, coupling two genuinely separate concerns
/// (is the audio quiet vs. has the reply watchdog expired) into one
/// function. `checkForQuiet()` now just calls `extend()`; this type owns
/// deciding whether that's still allowed.
@MainActor
private final class ExtendableDeadline {
    private let duration: TimeInterval
    private let maxTotalDuration: TimeInterval
    private let onExpired: () async -> Void

    private var task: Task<Void, Never>?
    private var startedAt: Date?

    init(duration: TimeInterval, maxTotalDuration: TimeInterval, onExpired: @escaping () async -> Void) {
        self.duration = duration
        self.maxTotalDuration = maxTotalDuration
        self.onExpired = onExpired
    }

    /// Starts the deadline running from now. Safe to call again later (e.g.
    /// a new voice turn) -- resets `startedAt`, so `maxTotalDuration` is
    /// measured from this call, not some earlier one.
    func start() {
        startedAt = Date()
        reschedule()
    }

    /// Pushes the deadline `duration` seconds further out from now -- but
    /// only while still within `maxTotalDuration` of the `start()` call.
    /// Past that, this is a no-op: whatever's already scheduled fires on
    /// its own, exactly as if `extend()` were never called again. Also a
    /// no-op if `start()` was never called (nothing to extend).
    func extend() {
        guard let startedAt, Date().timeIntervalSince(startedAt) < maxTotalDuration else { return }
        reschedule()
    }

    /// Stops the deadline entirely -- no `onExpired` call, nothing pending.
    func cancel() {
        task?.cancel()
        task = nil
        startedAt = nil
    }

    private func reschedule() {
        task?.cancel()
        task = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64((self?.duration ?? 0) * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            await self.onExpired()
        }
    }
}

// MARK: - Remote audio capture renderer

/// Taps PCM frames from the subscribed reply track for local replay
/// buffering, alongside (not instead of) the SDK's own automatic playback.
///
/// Held as a strong property on `VoiceSessionController` per a confirmed real
/// SDK gotcha (github.com/livekit/client-sdk-swift issue #350): `AudioTrack`
/// wraps added renderers in an internal `AudioRendererAdapter` backed by an
/// `NSHashTable.weakObjects()` (confirmed in `MulticastDelegate.swift`), so a
/// renderer with no other strong reference is deallocated almost immediately
/// and silently stops receiving frames.
///
/// Not `@MainActor`: `AudioRenderer.render(pcmBuffer:)` fires on a real-time
/// audio thread, same rationale as `VoiceTranscriptionService`'s own tap
/// callback -- mutable state here is guarded by `lock`, matching that file's
/// established pattern in this codebase.
private final class ReplayAudioRenderer: NSObject, AudioRenderer, @unchecked Sendable {
    /// Called on every frame, off the main thread, with that frame's peak
    /// amplitude (0...1) -- callers are expected to hop to whatever actor
    /// they need themselves (see `VoiceSessionController.init`). Passing
    /// amplitude through here (rather than a bare `Void` signal) is what
    /// lets a caller distinguish real speech from the continuous low-level
    /// stream WebRTC delivers even during silence -- see
    /// `VoiceSessionController.audioFrameReceived`'s doc comment.
    var onFrame: ((Float) -> Void)?

    private let lock = NSLock()
    private var buffers: [AVAudioPCMBuffer] = []

    func render(pcmBuffer: AVAudioPCMBuffer) {
        let peak = Self.peakAmplitude(pcmBuffer)
        if let copy = Self.copy(pcmBuffer) {
            lock.lock()
            buffers.append(copy)
            lock.unlock()
        }
        onFrame?(peak)
    }

    /// Max absolute sample value across all channels, normalized to 0...1 --
    /// the signal `VoiceSessionController.audioFrameReceived` uses to tell
    /// real speech apart from the continuous low-level stream WebRTC
    /// delivers even during silence (confirmed live via temporary
    /// diagnostic logging: frames arriving every ~10ms with zero gap for
    /// the ENTIRE `.awaitingReply` window, not just while actually
    /// speaking -- a pure frame-arrival-gap heuristic can never detect
    /// quiet against that). LiveKit/WebRTC delivers Int16 PCM in practice
    /// (confirmed live: `floatChannelData` was `nil` for every single frame
    /// logged), so Int16 is checked first, not as a fallback -- an
    /// AVAudioPCMBuffer only ever populates ONE of these three based on its
    /// own `format`, never more than one.
    private static func peakAmplitude(_ buffer: AVAudioPCMBuffer) -> Float {
        let frameCount = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frameCount > 0, channelCount > 0 else { return 0 }

        if let channelData = buffer.int16ChannelData {
            var peak: Int16 = 0
            for channel in 0..<channelCount {
                let samples = channelData[channel]
                for i in 0..<frameCount {
                    peak = max(peak, samples[i].magnitude == Int16.min.magnitude ? Int16.max : abs(samples[i]))
                }
            }
            return Float(peak) / Float(Int16.max)
        }
        if let channelData = buffer.floatChannelData {
            var peak: Float = 0
            for channel in 0..<channelCount {
                let samples = channelData[channel]
                for i in 0..<frameCount {
                    peak = max(peak, abs(samples[i]))
                }
            }
            return peak
        }
        return 0
    }

    /// Returns everything captured since the last drain and clears the
    /// internal buffer, so the next reply starts fresh.
    func drainBuffers() -> [AVAudioPCMBuffer] {
        lock.lock()
        defer { lock.unlock() }
        let drained = buffers
        buffers.removeAll()
        return drained
    }

    /// `AVAudioPCMBuffer` doesn't conform to `NSCopying` -- frames handed to
    /// `render(pcmBuffer:)` are owned by the SDK/WebRTC and may be reused
    /// once this call returns, so they must be deep-copied to survive until
    /// replay.
    private static func copy(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameCapacity) else {
            return nil
        }
        copy.frameLength = buffer.frameLength
        let frameCount = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)

        if let source = buffer.floatChannelData, let destination = copy.floatChannelData {
            for channel in 0..<channelCount {
                destination[channel].update(from: source[channel], count: frameCount)
            }
        } else if let source = buffer.int16ChannelData, let destination = copy.int16ChannelData {
            for channel in 0..<channelCount {
                destination[channel].update(from: source[channel], count: frameCount)
            }
        } else if let source = buffer.int32ChannelData, let destination = copy.int32ChannelData {
            for channel in 0..<channelCount {
                destination[channel].update(from: source[channel], count: frameCount)
            }
        }
        return copy
    }
}

// MARK: - Wire types

private struct VoiceIncomingEnvelope: Decodable {
    let type: String
}

private struct VoiceTokenResponse: Decodable {
    let url: String
    let roomName: String
    let token: String
}

/// Property names double as the exact JSON keys the backend expects
/// (`type`/`text`) -- no `keyEncodingStrategy` is set on the encoder used to
/// serialize this, so nothing rewrites them. `text` is `Optional` so that
/// `speech_started`/`speech_stopped` messages omit the key entirely
/// (Swift's synthesized `Encodable` conformance calls `encodeIfPresent` for
/// `Optional` properties), matching the backend's documented message shape
/// exactly rather than sending `"text": null`.
private struct VoiceDataMessage: Encodable {
    let type: String
    let text: String?
}

/// Milestone 7.5 (mode continuity, FR9): one-time outgoing message carrying
/// recent text-chat history, published right after connecting and before
/// on-device transcription starts (see `publishContextSeed(from:)`). A
/// second, distinct wire type from `VoiceDataMessage` above -- it carries a
/// nested array (`messages`) rather than that struct's flat `type`/`text`
/// shape -- but follows the exact same pattern: property names double as
/// the JSON keys the backend's `app/voice_transcript_bridge.py` expects
/// (`type`/`messages`/`role`/`content`), with no `keyEncodingStrategy`
/// rewriting them.
private struct ContextSeedMessage: Encodable {
    let type: String
    let messages: [ContextSeedEntry]
}

private struct ContextSeedEntry: Encodable {
    let role: String
    let content: String
}

private struct LocationSeedMessage: Encodable {
    let type: String
    let location: String
}

private enum VoiceSessionError: LocalizedError {
    case tokenFetchFailed(String)

    var errorDescription: String? {
        switch self {
        case .tokenFetchFailed(let message):
            return message
        }
    }
}
