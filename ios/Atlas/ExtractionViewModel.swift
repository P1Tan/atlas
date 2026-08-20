import Foundation

@MainActor
final class ExtractionViewModel: ObservableObject {
    @Published var draftEvents: [DraftEvent] = []
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var hasSearched = false

    private let extractURL = URL(string: "http://127.0.0.1:8000/extract")!

    func extract(text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        let referenceDate = Date()
        let referenceFormatter = ISO8601DateFormatter()
        referenceFormatter.formatOptions = [.withInternetDateTime]

        let request = ExtractRequest(
            text: trimmed,
            referenceDatetime: referenceFormatter.string(from: referenceDate),
            timezone: TimeZone.current.identifier
        )

        do {
            var urlRequest = URLRequest(url: extractURL)
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

            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            decoder.dateDecodingStrategy = .iso8601
            let events = try decoder.decode([ExtractedEvent].self, from: data)
            draftEvents = events.map { DraftEvent(from: $0, fallbackStart: referenceDate) }
            hasSearched = true
        } catch {
            errorMessage = error.localizedDescription
            hasSearched = true
        }
    }
}
