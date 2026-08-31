import Foundation

@MainActor
final class MemoryViewModel: ObservableObject {
    @Published var facts: [Fact] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let baseURL = "http://127.0.0.1:8000"

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

    private func restore(_ fact: Fact, at index: Int?) {
        facts.insert(fact, at: min(index ?? facts.count, facts.count))
    }
}
