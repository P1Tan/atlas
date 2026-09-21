import Foundation
import UIKit

/// Where a voice turn currently sits, driving `ChatView`'s voice UI.
/// - `idle`: no voice turn in progress; mic button is available.
/// - `listening`: mic is capturing; `liveInterimTranscript` updates live.
/// - `thinking`: the final transcript has been sent; waiting on the
///   backend's reply (text and/or the first audio byte, whichever first).
/// - `speaking`: the assistant's reply audio is actively playing.
enum VoiceState {
    case idle
    case listening
    case thinking
    case speaking
}

/// The one handle the rest of the app has on an in-progress voice turn.
///
/// Found by review: sign-out leaked a live voice session. `ChatViewModel` is
/// a `@StateObject` private to `ChatView`, but sign-out happens entirely
/// outside it -- `AuthViewModel.signOut()` flips `isSignedIn`, `ContentView`
/// swaps `MainTabView` for `SignInView`, and the whole chat tab (view model,
/// `VoiceSessionController`, connected LiveKit room, live mic capture) is
/// released with nothing ever disconnecting any of it. Worse than the leaked
/// room: `UIApplication.shared.isIdleTimerDisabled` stayed pinned to `true`,
/// because the only code that ever sets it back to `false` is the
/// `voiceState.didSet` on the object that just got deallocated -- so the
/// screen stopped auto-locking for the rest of the process.
///
/// Why this shape:
/// - It has to run BEFORE the UI is torn down, so it hangs off the sign-out
///   action itself. Hooking `ChatView.onDisappear` would be wrong: SwiftUI's
///   `TabView` fires `onDisappear` on every ordinary tab switch, which would
///   kill a voice turn just because the user glanced at the Memory tab.
/// - It isn't a `deinit`. `ChatViewModel` is `@MainActor` but `deinit` is
///   not actor-isolated, so touching `UIApplication` (or awaiting a room
///   disconnect) from there isn't safe, and by then the room is unreachable
///   anyway.
/// - It's a registration rather than a direct reference because
///   `AuthViewModel` (an `@EnvironmentObject` created at app launch) must
///   not know about, or outlive-capture, a view model owned by one tab's
///   view. Whichever `ChatViewModel` is currently alive registers itself;
///   the closure holds it weakly, so this never keeps a torn-down view model
///   alive.
///
/// Deliberately NOT paired with disabling the sign-out button while a voice
/// turn is live, the other fix the review offered: continuous-conversation
/// mode auto-restarts listening after every reply, so `voiceState` is
/// non-`.idle` for most of a conversation -- disabling sign-out on that
/// condition would leave a user who just wants out repeatedly tapping a
/// dead button. Signing out mid-turn is a legitimate thing to do; it just
/// has to clean up after itself, which is what this does.
@MainActor
final class ActiveVoiceSession {
    static let shared = ActiveVoiceSession()

    private var endHandler: (() async -> Void)?

    private init() {}

    /// Called by each `ChatViewModel` as it comes up. Last registration
    /// wins, which is exactly right: only one chat tab exists at a time, and
    /// a fresh sign-in creates a fresh view model that replaces the entry
    /// left by the previous one.
    func register(endHandler: @escaping () async -> Void) {
        self.endHandler = endHandler
    }

    /// Awaited by `AuthViewModel.signOut()` before it flips `isSignedIn`.
    /// A no-op when no voice turn is in progress (or before any view model
    /// has registered), so callers don't need to check first.
    func endIfActive() async {
        await endHandler?()
    }
}

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published private(set) var isSending = false
    @Published private(set) var errorMessage: String?

    // Found live, repeatedly: a voice turn that sat untouched while
    // listening/waiting for/playing a reply would let the screen auto-lock,
    // which suspends the app's RunLoop timers and Task.sleep-based logic
    // (VoiceSessionController's quiet-detection timer and its 30s
    // awaitingReplyTimeout both rely on one or the other) -- confirmed via
    // the backend log twice: a reply finished normally, then the room sat
    // open and completely silent for minutes with no teardown, no timeout,
    // nothing, until the connection eventually died server-side. Disabling
    // the idle timer for the duration of any active voice state prevents
    // the auto-lock that causes this in the first place, the same way every
    // call/voice-assistant app does. Deliberately scoped to "any non-.idle
    // state," not just .listening -- the freeze was observed happening
    // during .speaking (waiting out a reply) just as much as .listening.
    @Published private(set) var voiceState: VoiceState = .idle {
        didSet {
            guard voiceState != oldValue else { return }
            UIApplication.shared.isIdleTimerDisabled = voiceState != .idle
        }
    }
    /// Live preview of the in-progress utterance while `voiceState ==
    /// .listening` -- never appended to `messages` itself, only the final
    /// committed transcript becomes a real `ChatMessage` (see
    /// `handleFinalTranscript`).
    @Published var liveInterimTranscript: String?
    private var isStartingVoiceTurn = false

    /// Set once by `ChatView` (it owns `AuthViewModel`, which this view
    /// model deliberately doesn't hold a reference to -- same reasoning as
    /// every other `accessToken` in this file being handed in per-call
    /// rather than fetched here). Used only for continuous-conversation
    /// auto-restart (`onReplyCompleted` below), which has no UI event to
    /// hand a fresh token in through the way every other caller of
    /// `startVoiceTurn` does.
    var accessTokenProvider: (() async -> String?)?

    private let baseURL = AtlasAPI.baseURL
    private let reminderScheduler = ReminderScheduler()
    /// Shared by both send() (text) and startVoiceTurn() (voice) so there's
    /// one CLLocationManager/permission flow, not two independent ones.
    private let locationProvider = LocationProvider()

    /// Owns the LiveKit/on-device-STT plumbing; drives this view model's
    /// `@Published` state via the callback closures wired up in `init`,
    /// rather than being an `ObservableObject` of its own -- text and voice
    /// turns share this one view model's `messages` as their single source
    /// of truth (Milestone 7.4b).
    private let voiceController = VoiceSessionController()

    private static let responseDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private static let requestEncoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }()

    init() {
        voiceController.onSpeechStarted = { [weak self] in
            self?.voiceState = .listening
        }
        voiceController.onInterimTranscript = { [weak self] text in
            self?.liveInterimTranscript = text
        }
        voiceController.onFinalTranscript = { [weak self] text in
            self?.handleFinalTranscript(text)
        }
        // No onSpeechStopped handler needed: the transition out of
        // .listening happens in handleFinalTranscript once the final
        // transcript text itself is known, not on the raw turn-boundary
        // event alone.
        voiceController.onAssistantReply = { [weak self] text in
            self?.handleAssistantReply(text)
        }
        voiceController.onToolResult = { [weak self] message in
            Task { await self?.handleVoiceToolResult(message) }
        }
        voiceController.onPlaybackStarted = { [weak self] in
            // Finding 4 (code review, LOW-MEDIUM): also allow re-entry from
            // `.idle`, not just `.thinking`. If the "gone quiet" heuristic
            // fires prematurely on a natural mid-sentence pause,
            // `onPlaybackStopped` below moves voiceState to `.idle`; when
            // audio then resumes moments later (the same reply, per
            // `VoiceSessionController`'s own false-alarm handling), this
            // callback fires again and must be able to restore `.speaking`
            // from `.idle`, not just from `.thinking`. Without this, the
            // stop/replay controls would vanish for the rest of that reply
            // and the mic would re-enable mid-playback.
            guard let self, self.voiceState == .thinking || self.voiceState == .idle else { return }
            self.voiceState = .speaking
        }
        voiceController.onPlaybackStopped = { [weak self] in
            guard let self, self.voiceState == .speaking else { return }
            self.voiceState = .idle
        }
        // Continuous-conversation mode: auto-start the next turn once a
        // reply has genuinely finished, rather than requiring another mic
        // tap. Deliberately keyed off this dedicated callback rather than
        // `onPlaybackStopped` above -- that one also fires on ordinary
        // mid-sentence pauses (corrected a moment later by another
        // `onPlaybackStarted`), so using it here would race a fresh
        // `startVoiceTurn()` against audio that's about to resume for the
        // CURRENT reply. `onReplyCompleted` only fires once, from
        // `VoiceSessionController`'s one genuinely-successful-completion
        // path -- never for a timeout, a pipeline error, an interruption, or
        // an explicit user cancel (see its doc comment).
        voiceController.onReplyCompleted = { [weak self] in
            guard let self else { return }
            Task {
                let accessToken = await self.accessTokenProvider?()
                await self.startVoiceTurn(accessToken: accessToken)
            }
        }
        // Found live: without this, a turn that ended abnormally (the 30s
        // no-reply timeout, a pipeline_error, or an interruption while
        // awaiting a reply -- e.g. the app losing foreground execution
        // mid-reply) left voiceState stuck at .thinking/.speaking forever,
        // since nothing else on these paths ever moves it back. Both of
        // those states disable the mic button, so this wasn't just a stale
        // UI label -- it made the mic permanently unusable until the app was
        // relaunched. onError above already surfaces *why* to the user; this
        // is what actually makes the mic usable again afterward.
        voiceController.onReplyFailed = { [weak self] in
            guard let self, self.voiceState != .idle else { return }
            self.voiceState = .idle
        }
        voiceController.onError = { [weak self] message in
            self?.errorMessage = message
        }
        // See `ActiveVoiceSession`: makes an in-progress voice turn
        // reachable from `AuthViewModel.signOut()`, which happens outside
        // this view model's view tree and would otherwise release it (and
        // its live room) mid-turn with no teardown.
        ActiveVoiceSession.shared.register { [weak self] in
            await self?.endVoiceSessionForSignOut()
        }
    }

    /// Ends any in-progress voice turn because the object graph that owns it
    /// is about to disappear (sign-out). Runs while this view model is still
    /// alive and the room is still reachable -- see `ActiveVoiceSession` for
    /// why it's hooked to the sign-out action rather than to a view
    /// lifecycle event or a `deinit`.
    func endVoiceSessionForSignOut() async {
        await voiceController.endSessionForSignOut()
        liveInterimTranscript = nil
        voiceState = .idle
        // Not redundant with the `didSet` above: that only runs when the
        // value actually CHANGES, so it wouldn't fire if `voiceState` were
        // already `.idle` -- and the failure modes that leave the flag
        // stuck are exactly the odd ones. This is the last chance to hand
        // the screen back its ability to auto-lock, so it's unconditional.
        UIApplication.shared.isIdleTimerDisabled = false
    }

    func send(_ text: String, accessToken: String?) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        errorMessage = nil
        messages.append(ChatMessage(role: .user, content: trimmed))
        isSending = true
        defer { isSending = false }

        let request = ChatRequest(
            messages: messages,
            referenceDatetime: Self.isoString(from: Date()),
            timezone: TimeZone.current.identifier,
            userLocation: await locationProvider.currentLocationDescription()
        )

        do {
            var urlRequest = URLRequest(url: URL(string: "\(baseURL)/chat")!)
            urlRequest.httpMethod = "POST"
            urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
            if let accessToken {
                urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
            }
            urlRequest.httpBody = try Self.requestEncoder.encode(request)

            let (data, response) = try await URLSession.shared.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                return
            }
            guard http.statusCode == 200 else {
                switch http.statusCode {
                case 401:
                    errorMessage = "Your session expired. Please sign in again."
                case 429:
                    // Milestone 9.3: the backend's two distinct 429 cases
                    // (burst rate limit vs. daily usage cap) already carry
                    // a specific, actionable `detail` message -- surface
                    // that rather than collapsing both into one generic
                    // string, falling back only if the body doesn't decode.
                    errorMessage = Self.decodeErrorDetail(from: data)
                        ?? "You're sending messages too quickly. Please wait a moment and try again."
                default:
                    errorMessage = "Chat failed (server returned \(http.statusCode))."
                }
                return
            }

            let decoded = try Self.responseDecoder.decode(ChatResponse.self, from: data)
            messages.append(contentsOf: decoded.newMessages)
            await scheduleAnyReminders(from: decoded.newMessages)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: - Voice turn

    /// Starts a voice turn: fetches a fresh access token the same way `send`
    /// does, then hands off to `voiceController`. Sets `voiceState` to
    /// `.listening` on success, or leaves it `.idle` with `errorMessage` set
    /// on failure (permission denial, token fetch failure, room connect
    /// failure).
    func startVoiceTurn(accessToken: String?) async {
        // Milestone 8.1 introduced a second, automatic caller of this method
        // (launch-to-listen) alongside the existing manual mic-tap caller.
        // `voiceState` itself isn't updated until the `await` below returns,
        // so without this synchronous flag two near-simultaneous callers can
        // both pass the `voiceState == .idle` guard; the loser would then
        // hit `VoiceSessionController`'s own "already connecting" early
        // return and optimistically flip to `.listening` even though it
        // did nothing -- if the real connection later failed, that caller's
        // UI would be stuck showing "Listening…" with no active session.
        guard voiceState == .idle, !isStartingVoiceTurn else { return }
        isStartingVoiceTurn = true
        defer { isStartingVoiceTurn = false }
        errorMessage = nil
        liveInterimTranscript = nil
        let location = await locationProvider.currentLocationDescription()
        let started = await voiceController.startVoiceTurn(
            accessToken: accessToken, priorMessages: messages, location: location
        )
        voiceState = started ? .listening : .idle
    }

    /// Normal completion (tap-to-stop): lets whatever was captured finalize
    /// and send normally, same as `speechStopped` firing on its own.
    func stopVoiceTurn() async {
        guard voiceState == .listening else { return }
        await voiceController.stopVoiceTurn()
        // If a final transcript arrived during the drain above,
        // handleFinalTranscript already moved voiceState to .thinking. If
        // nothing was ever captured (e.g. stop tapped immediately), fall
        // back to idle rather than getting stuck "listening" forever.
        if voiceState == .listening {
            voiceState = .idle
        }
        liveInterimTranscript = nil
    }

    /// Instant cancel: aborts the in-progress turn WITHOUT sending anything
    /// captured so far -- distinct from `stopVoiceTurn()`, surfaced in the
    /// UI as a separate, always-visible action while listening.
    func cancelVoiceTurn() async {
        guard voiceState == .listening else { return }
        await voiceController.cancelVoiceTurn()
        liveInterimTranscript = nil
        voiceState = .idle
    }

    func stopPlayback() {
        voiceController.stopPlayback()
    }

    func replayLastReply() {
        voiceController.replayLastReply()
    }

    private func handleFinalTranscript(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        liveInterimTranscript = nil
        guard !trimmed.isEmpty else {
            voiceState = .idle
            return
        }
        messages.append(ChatMessage(role: .user, content: trimmed))
        voiceState = .thinking
    }

    private func handleAssistantReply(_ text: String) {
        messages.append(ChatMessage(role: .assistant, content: text))
        if voiceState == .thinking {
            voiceState = .speaking
        }
    }

    private func handleVoiceToolResult(_ message: ToolResultMessage) async {
        guard message.name == "set_reminder" else { return }
        await applyReminderResult(message.result)
    }

    /// The model's own reply already tells the user a reminder was set, in
    /// natural language -- but that text is only true if this actually
    /// succeeds. Surface it plainly if the real, on-device schedule fails,
    /// rather than letting a confident-sounding reply stand uncorrected.
    /// Shared by both the text-chat path (`scheduleAnyReminders`) and the
    /// voice path (`handleVoiceToolResult`) -- the backend's `set_reminder`
    /// tool result has the exact same shape either way.
    private func scheduleAnyReminders(from newMessages: [ChatMessage]) async {
        for message in newMessages where message.role == .tool && message.name == "set_reminder" {
            guard let content = message.content, let data = content.data(using: .utf8) else { continue }
            guard let result = try? Self.responseDecoder.decode(SetReminderToolResult.self, from: data) else {
                continue
            }
            await applyReminderResult(result)
        }
    }

    private func applyReminderResult(_ result: SetReminderToolResult) async {
        guard result.ok, let title = result.title, let triggerTime = result.triggerTime else { return }

        switch await reminderScheduler.schedule(title: title, triggerTimeISO8601: triggerTime) {
        case .success:
            break
        case .permissionDenied:
            errorMessage = "Reminder set, but notifications are disabled. Enable them in Settings > Atlas to be alerted."
        case .failure(let message):
            errorMessage = "Reminder set, but scheduling the notification failed: \(message)"
        }
    }

    private static func isoString(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }

    /// FastAPI's `HTTPException(detail: ...)` shape (`{"detail": "..."}`).
    /// `nil` on any decode failure -- callers fall back to a generic message
    /// rather than surfacing a decode error for what's already an error.
    private static func decodeErrorDetail(from data: Data) -> String? {
        struct ErrorBody: Decodable { let detail: String }
        return try? JSONDecoder().decode(ErrorBody.self, from: data).detail
    }
}
