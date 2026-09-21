import Foundation
import Supabase

@MainActor
final class AuthViewModel: ObservableObject {
    @Published private(set) var isSignedIn = false
    @Published private(set) var isSendingLink = false
    @Published private(set) var linkSent = false
    @Published var errorMessage: String?

    let client: SupabaseClient

    init() {
        // See FileAuthLocalStorage's doc comment: this project builds Debug
        // with code signing disabled, which makes the SDK's default Keychain
        // storage fail silently (setSession() reports success while nothing
        // is actually persisted). File-backed storage sidesteps that -- but
        // only for Debug. A signed Release build (should one ever exist) must
        // get real Keychain storage automatically, not depend on someone
        // remembering to revert this -- security review flagged the
        // unconditional version as a real gap (a stolen plaintext session
        // survives even unencrypted device backups, which Keychain items are
        // specifically excluded from).
        #if DEBUG
        let authStorage: any AuthLocalStorage = FileAuthLocalStorage()
        #else
        let authStorage: any AuthLocalStorage = KeychainLocalStorage()
        #endif
        client = SupabaseClient(
            supabaseURL: SupabaseConfig.url,
            supabaseKey: SupabaseConfig.anonKey,
            options: SupabaseClientOptions(
                auth: SupabaseClientOptions.AuthOptions(storage: authStorage)
            )
        )
    }

    /// Restores a session already persisted from a previous launch, or --
    /// debug/test builds only -- a session injected via launch environment so
    /// XCUITest can bypass the real magic-link email round trip.
    func bootstrap() async {
        #if DEBUG
        let environment = ProcessInfo.processInfo.environment

        // Lets a UI test exercise the signed-out SignInView deterministically,
        // even though a real session may be sitting in FileAuthLocalStorage
        // from an earlier run (unlike a real uninstall, xcodebuild test's
        // reinstall between runs doesn't necessarily clear app data).
        if environment["ATLAS_TEST_FORCE_SIGNED_OUT"] != nil {
            try? await client.auth.signOut()
            isSignedIn = false
            return
        }

        if let accessToken = environment["ATLAS_TEST_ACCESS_TOKEN"],
            let refreshToken = environment["ATLAS_TEST_REFRESH_TOKEN"]
        {
            do {
                _ = try await client.auth.setSession(accessToken: accessToken, refreshToken: refreshToken)
                isSignedIn = true
                return
            } catch {
                errorMessage = "Test session injection failed: \(error.localizedDescription)"
            }
        }
        #endif

        isSignedIn = client.auth.currentSession != nil
    }

    /// Lets the user back out of the "check your email" state to correct a
    /// mistyped address, rather than being stuck until they force-quit.
    func resetLinkSent() {
        linkSent = false
        errorMessage = nil
    }

    func sendMagicLink(to email: String) async {
        errorMessage = nil
        isSendingLink = true
        defer { isSendingLink = false }

        // Found live: SignInView's own "disabled" check trims whitespace
        // before deciding the button is tappable, but the untrimmed field
        // value was what actually got sent -- leading/trailing whitespace
        // (common from autofill/paste) could pass that "looks valid" check
        // yet still reach Supabase's API with it intact. Trimmed here, at
        // the boundary this class actually owns, rather than trusting every
        // caller to have already done it.
        let trimmedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)

        do {
            try await client.auth.signInWithOTP(email: trimmedEmail, redirectTo: SupabaseConfig.authCallbackURL)
            linkSent = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Completes sign-in from the magic-link deep link (atlas://login-callback#...).
    func handle(url: URL) async {
        do {
            _ = try await client.auth.session(from: url)
            isSignedIn = true
            errorMessage = nil
        } catch {
            errorMessage = "Sign-in failed: \(error.localizedDescription)"
        }
    }

    /// A guaranteed-valid access token for an authenticated backend request,
    /// refreshing first if the stored session has expired. Nil if signed out.
    func currentAccessToken() async -> String? {
        try? await client.auth.session.accessToken
    }

    func signOut() async {
        // Found by review: this flip of `isSignedIn` makes `ContentView`
        // swap `MainTabView` for `SignInView`, which releases `ChatView`'s
        // `@StateObject ChatViewModel` -- and, with it, a possibly-live
        // LiveKit room, active mic capture, and the only code that ever
        // resets `UIApplication.shared.isIdleTimerDisabled` back to `false`
        // (so the screen stopped auto-locking for the rest of the process).
        // Ending the voice turn first, here on the sign-out action itself,
        // is what makes that teardown happen while the room is still
        // reachable; see `ActiveVoiceSession` for why it's hooked here and
        // not to a view lifecycle event. A no-op when no voice turn is in
        // progress, which is the overwhelmingly common case.
        await ActiveVoiceSession.shared.endIfActive()

        try? await client.auth.signOut()
        isSignedIn = false
        linkSent = false
    }
}
