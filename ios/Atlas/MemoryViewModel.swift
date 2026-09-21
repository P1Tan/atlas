import Foundation

@MainActor
final class MemoryViewModel: ObservableObject {
    @Published var facts: [Fact] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let baseURL = AtlasAPI.baseURL

    private static let responseDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    func load(accessToken: String?) async {
        errorMessage = nil
        isLoading = true
        defer { isLoading = false }

        do {
            var urlRequest = URLRequest(url: URL(string: "\(baseURL)/facts")!)
            urlRequest.httpMethod = "GET"
            if let accessToken {
                urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
            }

            let (data, response) = try await URLSession.shared.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                return
            }
            guard http.statusCode == 200 else {
                errorMessage =
                    http.statusCode == 401
                    ? "Your session expired. Please sign in again."
                    : "Failed to load memories (server returned \(http.statusCode))."
                return
            }

            facts = try Self.responseDecoder.decode([Fact].self, from: data)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Removes `fact` from `facts` immediately (so the native swipe-to-delete
    /// animation stays in sync with the tap, matching ChatViewModel.send's
    /// existing optimistic-append precedent) rather than waiting for the
    /// network round trip -- and reinserts it at its original position if
    /// the delete turns out not to have actually happened.
    func delete(_ fact: Fact, accessToken: String?) async {
        errorMessage = nil
        let originalIndex = facts.firstIndex { $0.id == fact.id }
        facts.removeAll { $0.id == fact.id }

        do {
            var urlRequest = URLRequest(url: URL(string: "\(baseURL)/facts/\(fact.id)")!)
            urlRequest.httpMethod = "DELETE"
            if let accessToken {
                urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
            }

            let (_, response) = try await URLSession.shared.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                restore(fact, at: originalIndex)
                return
            }
            // 404 means it's already gone server-side -- leave it removed
            // locally too, just tell the user, rather than restoring
            // something that no longer exists.
            guard http.statusCode == 204 || http.statusCode == 404 else {
                errorMessage =
                    http.statusCode == 401
                    ? "Your session expired. Please sign in again."
                    : "Failed to delete (server returned \(http.statusCode))."
                restore(fact, at: originalIndex)
                return
            }
            if http.statusCode == 404 {
                errorMessage = "That memory was already removed."
            }
        } catch {
            errorMessage = error.localizedDescription
            restore(fact, at: originalIndex)
        }
    }

    /// PATCHes the edited text, showing it in the list right away and
    /// putting the original row back if the round trip doesn't land --
    /// `delete()`'s optimistic/restore pattern, one row over.
    func update(_ fact: Fact, newText: String, accessToken: String?) async {
        errorMessage = nil

        let trimmed = newText.trimmingCharacters(in: .whitespacesAndNewlines)
        // The backend answers 422 for both of these. MemoryEditSheet already
        // disables Save for them, so this guard only ever catches a
        // programmatic caller -- it just makes it impossible to spend a round
        // trip on a request that cannot succeed.
        guard !trimmed.isEmpty, trimmed.count <= Fact.maxTextLength else {
            errorMessage = "A memory must be 1 to \(Fact.maxTextLength) characters."
            return
        }

        guard let originalIndex = facts.firstIndex(where: { $0.id == fact.id }) else { return }
        let original = facts[originalIndex]
        facts[originalIndex] = Fact(id: original.id, factText: trimmed, createdAt: original.createdAt)

        do {
            var urlRequest = URLRequest(url: URL(string: "\(baseURL)/facts/\(fact.id)")!)
            urlRequest.httpMethod = "PATCH"
            urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
            if let accessToken {
                urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
            }
            urlRequest.httpBody = try JSONSerialization.data(withJSONObject: ["fact_text": trimmed])

            let (data, response) = try await URLSession.shared.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "No response from server."
                revert(to: original, at: originalIndex)
                return
            }
            // 404 means the row is gone server-side (deleted elsewhere), so
            // drop it locally rather than restoring something that no longer
            // exists -- the same call delete() makes for its own 404.
            if http.statusCode == 404 {
                errorMessage = "That memory was already removed."
                facts.removeAll { $0.id == fact.id }
                return
            }
            guard http.statusCode == 200 else {
                errorMessage =
                    http.statusCode == 401
                    ? "Your session expired. Please sign in again."
                    : "Failed to save (server returned \(http.statusCode))."
                revert(to: original, at: originalIndex)
                return
            }

            // Take the server's row as canonical rather than keeping the
            // locally-built one: it is what search_facts was re-embedded
            // from, so anything it normalised must be what the list shows.
            let updated = try Self.responseDecoder.decode(Fact.self, from: data)
            revert(to: updated, at: originalIndex)
        } catch {
            errorMessage = error.localizedDescription
            revert(to: original, at: originalIndex)
        }
    }

    /// Writes `fact` back over whatever row currently carries its id, falling
    /// back to `restore(_:at:)` if the row is gone entirely -- a swipe-delete
    /// or a pull-to-refresh during the round trip can reshuffle `facts`, so
    /// the index captured before the request isn't trustworthy on its own.
    private func revert(to fact: Fact, at index: Int) {
        if let current = facts.firstIndex(where: { $0.id == fact.id }) {
            facts[current] = fact
        } else {
            restore(fact, at: index)
        }
    }

    private func restore(_ fact: Fact, at index: Int?) {
        facts.insert(fact, at: min(index ?? facts.count, facts.count))
    }
}
