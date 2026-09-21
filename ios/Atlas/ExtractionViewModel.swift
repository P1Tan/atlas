import Foundation

@MainActor
final class ExtractionViewModel: ObservableObject {
    @Published var draftEvents: [DraftEvent] = []
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var hasSearched = false
    @Published private(set) var gmailConnected = false
    /// How many unread messages the last Gmail check skipped because they
    /// had already been reviewed -- the difference between "your inbox has
    /// nothing new" and "Atlas found nothing," which the user can't tell
    /// apart otherwise now that reviewed mail is silently excluded.
    @Published private(set) var skippedReviewedCount = 0

    private let baseURL = AtlasAPI.baseURL

    private static let responseDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)
            guard let date = ISO8601Parsing.date(from: string) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "Expected an ISO8601-formatted date string, got \(string)"
                )
            }
            return date
        }
        return decoder
    }()

    func extract(text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        isLoading = true
        errorMessage = nil
        // Shared-text extraction has nothing to do with Gmail, so clear the
        // count here too -- otherwise a share right after a Gmail check
        // would show that check's "skipped N already-reviewed" row next to
        // results that didn't come from email at all.
        skippedReviewedCount = 0
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

    /// `accessToken` is the caller's current Supabase access token --
    /// `/gmail/candidates` is an authenticated endpoint (it resolves the
    /// Gmail credential for the signed-in user), so the same
    /// `Authorization: Bearer` header `MemoryViewModel` sends is required
    /// here too. Passed in rather than fetched, because refreshing the
    /// session is `AuthViewModel`'s job and this view model has no business
    /// owning a second path to it.
    func checkGmail(accessToken: String?) async {
        struct GmailCandidatesResponse: Decodable {
            let candidates: [GmailCandidate]
            let skippedReviewedCount: Int
        }

        isLoading = true
        errorMessage = nil
        skippedReviewedCount = 0
        defer { isLoading = false }

        let referenceDate = Date()
        var components = URLComponents(string: "\(baseURL)/gmail/candidates")!
        var queryItems = [
            URLQueryItem(name: "reference_datetime", value: Self.isoString(from: referenceDate)),
            URLQueryItem(name: "timezone", value: TimeZone.current.identifier),
            URLQueryItem(name: "max_results", value: "10"),
        ]
        // Mail the user has already been shown is excluded server-side, so
        // it costs neither a fetch nor a per-message LLM extraction. 200 is
        // the backend's hard cap on this list (422 above it); the newest
        // ids are the ones that matter, since anything older has aged out
        // of the backend's own 30-day unread window.
        queryItems.append(contentsOf: ReviewedGmailMessages.ids(limit: 200).map {
            URLQueryItem(name: "exclude_message_ids", value: $0)
        })
        components.queryItems = queryItems

        do {
            // Found live: the backend extracts events from each unread
            // message sequentially through the LLM, one call per message --
            // a real inbox check (confirmed: 9 messages took ~64s) routinely
            // exceeds URLRequest's 60s default timeout, which surfaced as
            // "The request timed out" even though the backend had genuinely
            // succeeded moments later. 180s covers the worst case at
            // max_results' current value (10) with real margin, without
            // being effectively unbounded.
            var urlRequest = URLRequest(url: components.url!)
            urlRequest.timeoutInterval = 180
            if let accessToken {
                urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
            }
            let (data, response) = try await URLSession.shared.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                hasSearched = true
                return
            }
            guard http.statusCode == 200 else {
                if http.statusCode == 401 {
                    // Two unrelated failures now share this status code, and
                    // the difference matters to the user: a missing Gmail
                    // credential is fixed by connecting Gmail, an expired
                    // Supabase session by signing in again. Only the former
                    // may flip `gmailConnected` -- doing it for a stale
                    // session would swap the Check button for "Connect
                    // Gmail" and push the user into a pointless OAuth round
                    // trip that cannot fix an auth problem of a different
                    // kind. The body's `detail` is the only thing that tells
                    // them apart; an unrecognisable body is treated as the
                    // session case, which at least leaves state untouched.
                    struct ErrorDetail: Decodable { let detail: String }
                    let detail = try? JSONDecoder().decode(ErrorDetail.self, from: data).detail
                    if detail == "Gmail not connected" {
                        gmailConnected = false
                        errorMessage = "Gmail isn't connected."
                    } else {
                        errorMessage = "Your session has expired. Sign out and back in."
                    }
                } else {
                    errorMessage = "Gmail check failed (server returned \(http.statusCode))."
                }
                hasSearched = true
                return
            }

            let result = try Self.responseDecoder.decode(GmailCandidatesResponse.self, from: data)
            draftEvents = result.candidates.flatMap { candidate in
                candidate.events.map {
                    DraftEvent(from: $0, fallbackStart: referenceDate, sourceSubject: candidate.subject)
                }
            }
            // Every candidate counts as reviewed, including the ones that
            // yielded no events -- the user has now had them checked, and
            // re-extracting them on the next check would only burn LLM
            // calls to produce the same nothing.
            ReviewedGmailMessages.markReviewed(result.candidates.map(\.messageId))
            skippedReviewedCount = result.skippedReviewedCount
            hasSearched = true
        } catch {
            errorMessage = error.localizedDescription
            hasSearched = true
        }
    }

    /// The escape hatch behind "Show them again": forgets what's been
    /// reviewed so the next check offers previously reviewed mail again.
    /// Needed because the exclusion is otherwise permanent and invisible --
    /// e.g. the user dismissed an event by accident, or declined to add it
    /// and later changed their mind.
    func checkGmailIncludingReviewed(accessToken: String?) async {
        ReviewedGmailMessages.reset()
        await checkGmail(accessToken: accessToken)
    }

    private static func isoString(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}
