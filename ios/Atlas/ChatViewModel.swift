import Foundation

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published private(set) var isSending = false
    @Published private(set) var errorMessage: String?

    private let baseURL = "http://127.0.0.1:8000"

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

    func send(_ text: String) async {
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
            urlRequest.httpBody = try Self.requestEncoder.encode(request)

            let (data, response) = try await URLSession.shared.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                return
            }
            guard http.statusCode == 200 else {
                errorMessage = "Chat failed (server returned \(http.statusCode))."
                return
            }

            let decoded = try Self.responseDecoder.decode(ChatResponse.self, from: data)
            messages.append(contentsOf: decoded.newMessages)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private static func isoString(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}
