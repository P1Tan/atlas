import Foundation

@MainActor
final class ExtractionViewModel: ObservableObject {
    @Published var draftEvents: [DraftEvent] = []
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var hasSearched = false
    @Published private(set) var gmailConnected = false

    private let baseURL = "http://127.0.0.1:8000"

    private static let responseDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    func extract(text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        let referenceDate = Date()
        let request = ExtractRequest(
            text: trimmed,
            referenceDatetime: Self.isoString(from: referenceDate),
            timezone: TimeZone.current.identifier
        )

        do {
            var urlRequest = URLRequest(url: URL(string: "\(baseURL)/extract")!)
            urlRequest.httpMethod = "POST"
            urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")

            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            urlRequest.httpBody = try encoder.encode(request)

            let (data, response) = try await URLSession.shared.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                hasSearched = true
                return
            }
            guard http.statusCode == 200 else {
                errorMessage = "Extraction failed (server returned \(http.statusCode))."
                hasSearched = true
                return
            }

            let events = try Self.responseDecoder.decode([ExtractedEvent].self, from: data)
            draftEvents = events.map { DraftEvent(from: $0, fallbackStart: referenceDate) }
            hasSearched = true
        } catch {
            errorMessage = error.localizedDescription
            hasSearched = true
        }
    }

    func refreshGmailStatus() async {
        struct StatusResponse: Decodable { let connected: Bool }

        guard let url = URL(string: "\(baseURL)/auth/google/status") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            gmailConnected = try JSONDecoder().decode(StatusResponse.self, from: data).connected
        } catch {
            gmailConnected = false
        }
    }

    func checkGmail() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        let referenceDate = Date()
        var components = URLComponents(string: "\(baseURL)/gmail/candidates")!
        components.queryItems = [
            URLQueryItem(name: "reference_datetime", value: Self.isoString(from: referenceDate)),
            URLQueryItem(name: "timezone", value: TimeZone.current.identifier),
            URLQueryItem(name: "max_results", value: "10"),
        ]

        do {
            let (data, response) = try await URLSession.shared.data(from: components.url!)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                hasSearched = true
                return
            }
            guard http.statusCode == 200 else {
                if http.statusCode == 401 {
                    gmailConnected = false
                    errorMessage = "Gmail isn't connected."
                } else {
                    errorMessage = "Gmail check failed (server returned \(http.statusCode))."
                }
                hasSearched = true
                return
            }

            let candidates = try Self.responseDecoder.decode([GmailCandidate].self, from: data)
            draftEvents = candidates.flatMap { candidate in
                candidate.events.map {
                    DraftEvent(from: $0, fallbackStart: referenceDate, sourceSubject: candidate.subject)
                }
            }
            hasSearched = true
        } catch {
            errorMessage = error.localizedDescription
            hasSearched = true
        }
    }

    private static func isoString(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}
