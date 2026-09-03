import Foundation

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

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published private(set) var isSending = false
    @Published private(set) var errorMessage: String?

    @Published private(set) var voiceState: VoiceState = .idle
    /// Live preview of the in-progress utterance while `voiceState ==
    /// .listening` -- never appended to `messages` itself, only the final
    /// committed transcript becomes a real `ChatMessage` (see
    /// `handleFinalTranscript`).
    @Published var liveInterimTranscript: String?
    private var isStartingVoiceTurn = false

    private let baseURL = "http://127.0.0.1:8000"
    private let reminderScheduler = ReminderScheduler()

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
        voiceController.onError = { [weak self] message in
            self?.errorMessage = message
        }
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
            timezone: TimeZone.current.identifier
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
                errorMessage =
                    http.statusCode == 401
                    ? "Your session expired. Please sign in again."
                    : "Chat failed (server returned \(http.statusCode))."
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
        let started = await voiceController.startVoiceTurn(accessToken: accessToken, priorMessages: messages)
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
}
