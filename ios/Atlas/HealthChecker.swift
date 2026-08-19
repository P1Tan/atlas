import Foundation

enum HealthStatus: Equatable {
    case loading
    case ok(String)
    case failed(String)
}

private struct HealthResponse: Decodable {
    let status: String
}

@MainActor
final class HealthChecker: ObservableObject {
    @Published private(set) var status: HealthStatus = .loading

    private let healthURL = URL(string: "http://127.0.0.1:8000/health")!

    func check() async {
        status = .loading
        do {
            let (data, response) = try await URLSession.shared.data(from: healthURL)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                status = .failed("unexpected response")
                return
            }
            let decoded = try JSONDecoder().decode(HealthResponse.self, from: data)
            status = .ok(decoded.status)
        } catch {
            status = .failed(error.localizedDescription)
        }
    }
}
