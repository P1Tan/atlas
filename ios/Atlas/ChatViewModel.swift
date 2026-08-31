import Foundation

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published private(set) var isSending = false
    @Published private(set) var errorMessage: String?

    private let baseURL = "http://127.0.0.1:8000"
    private let reminderScheduler = ReminderScheduler()

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

    /// The model's own reply already tells the user a reminder was set, in
    /// natural language -- but that text is only true if this actually
    /// succeeds. Surface it plainly if the real, on-device schedule fails,
    /// rather than letting a confident-sounding reply stand uncorrected.
    private func scheduleAnyReminders(from newMessages: [ChatMessage]) async {
        for message in newMessages where message.role == .tool && message.name == "set_reminder" {
            guard let content = message.content, let data = content.data(using: .utf8) else { continue }
            guard let result = try? Self.responseDecoder.decode(SetReminderToolResult.self, from: data) else {
                continue
            }
            guard result.ok, let title = result.title, let triggerTime = result.triggerTime else { continue }

            switch await reminderScheduler.schedule(title: title, triggerTimeISO8601: triggerTime) {
            case .success:
                break
            case .permissionDenied:
                errorMessage = "Reminder set, but notifications are disabled. Enable them in Settings > Atlas to be alerted."
            case .failure(let message):
                errorMessage = "Reminder set, but scheduling the notification failed: \(message)"
            }
        }
    }

    private static func isoString(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}

private struct SetReminderToolResult: Decodable {
    let ok: Bool
    let title: String?
    let triggerTime: String?
    let reason: String?
}
