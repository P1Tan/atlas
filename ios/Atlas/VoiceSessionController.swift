import AVFoundation
import Foundation
import LiveKit

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
    var onError: ((String) -> Void)?

    private(set) var state: SessionState = .idle

    private let baseURL = "http://127.0.0.1:8000"
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
    private var quietCheckTimer: Timer?
    private var lastAudioFrameTime: Date?
    private let playbackQuietWindow: TimeInterval = 1.0

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
    /// the session a fixed time after listening stops, regardless of what
    /// did or didn't arrive, so the state machine can never get stuck in
    /// `.awaitingReply`.
    private var awaitingReplyTimeoutTask: Task<Void, Never>?
    private let awaitingReplyTimeout: TimeInterval = 30.0

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

    // MARK: - Audio session interruption / route-change handling (Milestone 9.2, §11)

    private var interruptionObserver: NSObjectProtocol?
    private var routeChangeObserver: NSObjectProtocol?
    /// Guards `endActiveSessionForInterruption` against firing twice for one
    /// real-world event -- a phone call can plausibly trigger both
    /// `interruptionNotification` and a `.oldDeviceUnavailable` route change
    /// in quick succession.
    private var isEndingSessionForInterruption = false

    override init() {
        super.init()
        replayRenderer.onFrame = { [weak self] in
            Task { @MainActor in self?.audioFrameReceived() }
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
    func startVoiceTurn(accessToken: String?, priorMessages: [ChatMessage]) async -> Bool {
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

            // Before any real utterance can possibly be published, seed the
            // backend's fresh voice-session LLM context with recent
            // text-chat history -- see publishContextSeed(from:).
            await publishContextSeed(from: priorMessages)

            try await transcriptionService.start()

            eventTask = Task { [weak self] in
                guard let self else { return }
                for await event in self.transcriptionService.events {
                    await self.handle(event)
                }
            }

            state = .listening
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
        scheduleAwaitingReplyTimeout()
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

    /// The single chokepoint that actually disconnects from the room --
    /// called from `cancelVoiceTurn()` (explicit bail-out) and
    /// `finishVoiceSessionAfterReply()` (the reply genuinely completed, or
    /// the `awaitingReplyTimeout` safety net fired). Also cancels any
    /// pending reply-completion bookkeeping so nothing fires after the room
    /// is gone.
    private func teardownRoom() async {
        awaitingReplyTimeoutTask?.cancel()
        awaitingReplyTimeoutTask = nil
        pendingTeardownTask?.cancel()
        pendingTeardownTask = nil

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

    private func scheduleAwaitingReplyTimeout() {
        awaitingReplyTimeoutTask?.cancel()
        awaitingReplyTimeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64((self?.awaitingReplyTimeout ?? 30) * 1_000_000_000))
            guard let self, !Task.isCancelled, self.state == .awaitingReply else { return }
            // Milestone 9.1 (NFR2): this timeout firing while still
            // `.awaitingReply` means total silence -- no `pipeline_error`
            // message arrived either (that path calls
            // `finishVoiceSessionAfterReply()` directly and tears the room
            // down, which cancels this task before it ever gets here), so
            // nothing has told the user what happened yet. Used to revert to
            // idle with zero explanation, a silent hang bounded only by this
            // 30s timeout rather than one that actually surfaces anything.
            if !(self.replyTextReceived && self.replyAudioFinished) {
                self.onError?("Atlas didn't reply in time. Please try again.")
            }
            await self.finishVoiceSessionAfterReply()
        }
    }

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
            await self.finishVoiceSessionAfterReply()
        }
    }

    /// Actually ends the voice session: tears down the room. Guarded on
    /// `state == .awaitingReply` so this is a no-op if `cancelVoiceTurn()`
    /// (or a second competing finish path) already tore the room down first.
    private func finishVoiceSessionAfterReply() async {
        guard state == .awaitingReply else { return }
        // Belt-and-suspenders: capture anything still sitting in the
        // renderer's buffer (e.g. the `awaitingReplyTimeout` safety net
        // fired without a `checkForQuiet()` ever having run) so
        // `replayLastReply()` has the fullest possible reply audio even in
        // that edge case.
        finalizeCurrentReplyBuffer()
        await teardownRoom()
        state = .stopped
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
    /// track. Restarts the "gone quiet" debounce window and, on the first
    /// frame since a quiet period, fires `onPlaybackStarted`.
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
    private func audioFrameReceived() {
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
        startQuietCheckTimerIfNeeded()
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
        guard Date().timeIntervalSince(lastAudioFrameTime) >= playbackQuietWindow else { return }
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
        var urlRequest = URLRequest(url: URL(string: "\(baseURL)/voice/token")!)
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: urlRequest)
        guard let http = response as? HTTPURLResponse else {
            throw VoiceSessionError.tokenFetchFailed("No response from server.")
        }
        guard http.statusCode == 200 else {
            throw VoiceSessionError.tokenFetchFailed(
                http.statusCode == 401
                    ? "Your session expired. Please sign in again."
                    : "Failed to start a voice session (server returned \(http.statusCode))."
            )
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
        case "pipeline_error":
            guard let message = try? Self.responseDecoder.decode(PipelineErrorMessage.self, from: data) else { return }
            // Guarded on .awaitingReply, mirroring scheduleAwaitingReplyTimeout's
            // own guard: a message delivered late (after a local timeout
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
    /// Called on every frame, off the main thread. Callers are expected to
    /// hop to whatever actor they need themselves (see `VoiceSessionController.init`).
    var onFrame: (() -> Void)?

    private let lock = NSLock()
    private var buffers: [AVAudioPCMBuffer] = []

    func render(pcmBuffer: AVAudioPCMBuffer) {
        if let copy = Self.copy(pcmBuffer) {
            lock.lock()
            buffers.append(copy)
            lock.unlock()
        }
        onFrame?()
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

private enum VoiceSessionError: LocalizedError {
    case tokenFetchFailed(String)

    var errorDescription: String? {
        switch self {
        case .tokenFetchFailed(let message):
            return message
        }
    }
}
