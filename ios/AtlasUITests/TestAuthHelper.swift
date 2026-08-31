import XCTest

/// Bypasses the real magic-link sign-in flow for UI tests by fetching a
/// session for a fixed, persistent Supabase test account
/// (`atlas-uitest@example.com`) via the password grant, then injecting the
/// resulting access/refresh tokens into the app's launch environment. The app
/// (see `AuthViewModel.bootstrap()`, DEBUG-only) reads
/// `ATLAS_TEST_ACCESS_TOKEN` / `ATLAS_TEST_REFRESH_TOKEN` and calls
/// `client.auth.setSession(accessToken:refreshToken:)` to sign in directly,
/// skipping the email round trip entirely.
///
/// This account exists solely for UI test automation -- it has no real data
/// or privileges beyond what Supabase Row Level Security scopes to its own
/// user id, so a fixed, shared password here carries no real security
/// exposure, the same way RLS-scoped test fixtures don't.
enum TestAuthHelper {
    private static let supabaseURL = URL(string: "https://pzkjispzeocfdjyapcff.supabase.co/auth/v1/token?grant_type=password")!
    private static let apiKey = "sb_publishable_Dr3HU-eVQ483ccVX9BkQ6g_rRzyo51e"
    private static let testEmail = "atlas-uitest@example.com"
    private static let testPassword = "AtlasUITestFixedPassword123!"

    /// Fetches a fresh access/refresh token pair for the fixed UI test
    /// account via Supabase's password grant.
    static func fetchTestSessionTokens() async throws -> (accessToken: String, refreshToken: String) {
        var request = URLRequest(url: supabaseURL)
        request.httpMethod = "POST"
        request.setValue(apiKey, forHTTPHeaderField: "apikey")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "email": testEmail,
            "password": testPassword,
        ])

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw NSError(
                domain: "TestAuthHelper",
                code: -1,
                userInfo: [NSLocalizedDescriptionKey: "Non-HTTP response fetching test session tokens."]
            )
        }

        guard httpResponse.statusCode == 200 else {
            let bodyText = String(data: data, encoding: .utf8) ?? "<non-UTF8 body>"
            throw NSError(
                domain: "TestAuthHelper",
                code: httpResponse.statusCode,
                userInfo: [
                    NSLocalizedDescriptionKey:
                        "Supabase token request failed with status \(httpResponse.statusCode): \(bodyText)"
                ]
            )
        }

        guard
            let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            let bodyText = String(data: data, encoding: .utf8) ?? "<non-UTF8 body>"
            throw NSError(
                domain: "TestAuthHelper",
                code: -2,
                userInfo: [NSLocalizedDescriptionKey: "Could not parse Supabase token response as JSON object: \(bodyText)"]
            )
        }

        guard let accessToken = json["access_token"] as? String else {
            let bodyText = String(data: data, encoding: .utf8) ?? "<non-UTF8 body>"
            throw NSError(
                domain: "TestAuthHelper",
                code: -3,
                userInfo: [NSLocalizedDescriptionKey: "Supabase token response missing access_token: \(bodyText)"]
            )
        }

        guard let refreshToken = json["refresh_token"] as? String else {
            let bodyText = String(data: data, encoding: .utf8) ?? "<non-UTF8 body>"
            throw NSError(
                domain: "TestAuthHelper",
                code: -4,
                userInfo: [NSLocalizedDescriptionKey: "Supabase token response missing refresh_token: \(bodyText)"]
            )
        }

        return (accessToken, refreshToken)
    }

    /// Fetches a session for the fixed UI test account, injects it into the
    /// app's launch environment, and launches the app already signed in.
    ///
    /// `AuthViewModel.bootstrap()` validates the injected tokens against
    /// Supabase asynchronously (a real network round trip) before flipping
    /// to the signed-in tab UI, so this also waits for that tab UI to
    /// appear -- otherwise callers that interact with the UI immediately
    /// after `launch()` would race the sign-in-screen-to-main-UI transition
    /// and intermittently fail on a still-visible sign-in screen.
    /// `@MainActor` because the network `await` inside otherwise has no
    /// guarantee of resuming on the main thread, and every `XCUIElement`
    /// call after it (`app.launch()`, `waitForExistence`, etc.) requires
    /// exactly that -- without this, the call crashes intermittently with
    /// "Must be called on the main thread" (NSInternalInconsistencyException).
    @MainActor
    static func launchSignedIn(_ app: XCUIApplication) async throws {
        let tokens = try await fetchTestSessionTokens()
        app.launchEnvironment["ATLAS_TEST_ACCESS_TOKEN"] = tokens.accessToken
        app.launchEnvironment["ATLAS_TEST_REFRESH_TOKEN"] = tokens.refreshToken
        app.launch()

        guard app.tabBars.firstMatch.waitForExistence(timeout: 20) else {
            throw NSError(
                domain: "TestAuthHelper",
                code: -5,
                userInfo: [
                    NSLocalizedDescriptionKey:
                        "Signed-in tab UI never appeared after launch -- test session injection likely failed."
                ]
            )
        }
    }

    /// Posts a single user message directly to the real `/chat` endpoint,
    /// bypassing the app UI entirely -- used to seed backend state (e.g. a
    /// fact via the `remember_fact` tool) before `app.launch()`, since there
    /// is no REST write path for facts and the only way to create one is a
    /// real conversational turn.
    static func sendChatMessage(_ text: String, accessToken: String) async throws {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let referenceDatetime = formatter.string(from: Date())

        var request = URLRequest(url: URL(string: "http://127.0.0.1:8000/chat")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "messages": [["role": "user", "content": text]],
            "reference_datetime": referenceDatetime,
            "timezone": "America/New_York",
        ])

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw NSError(
                domain: "TestAuthHelper",
                code: -1,
                userInfo: [NSLocalizedDescriptionKey: "Non-HTTP response seeding chat message."]
            )
        }

        guard httpResponse.statusCode == 200 else {
            let bodyText = String(data: data, encoding: .utf8) ?? "<non-UTF8 body>"
            throw NSError(
                domain: "TestAuthHelper",
                code: httpResponse.statusCode,
                userInfo: [
                    NSLocalizedDescriptionKey:
                        "Seeding /chat message failed with status \(httpResponse.statusCode): \(bodyText)"
                ]
            )
        }
    }
}
