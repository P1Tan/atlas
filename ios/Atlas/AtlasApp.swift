import SwiftUI
import UserNotifications

@main
struct AtlasApp: App {
    // Seeded, not assigned later, so the shared text is already waiting
    // before ContentView and PasteInputView mount -- their .task catch-ups
    // only fire once, and a value set after they attach would be missed.
    @StateObject private var shareInbox = ShareInbox(pendingText: AtlasApp.testShareText)
    @StateObject private var authViewModel = AuthViewModel()
    @StateObject private var launchCoordinator = LaunchCoordinator()

    /// Debug/test builds only: lets XCUITest hand the app text exactly as the
    /// share extension would, since the share sheet itself can't be driven
    /// from the app's own UI tests. Mirrors `AuthViewModel.bootstrap()`'s
    /// ATLAS_TEST_ACCESS_TOKEN injection; always nil in a Release build, so
    /// no shipping build can be fed text through the environment.
    private static var testShareText: String? {
        #if DEBUG
        let text = ProcessInfo.processInfo.environment["ATLAS_TEST_SHARE_TEXT"]
        return (text?.isEmpty ?? true) ? nil : text
        #else
        return nil
        #endif
    }

    init() {
        UNUserNotificationCenter.current().delegate = NotificationPresenter.shared
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(shareInbox)
                .environmentObject(authViewModel)
                .environmentObject(launchCoordinator)
                .task {
                    await authViewModel.bootstrap()
                }
                .onOpenURL { url in
                    if url.host == "login-callback" {
                        Task { await authViewModel.handle(url: url) }
                    } else if url.host == "listen" {
                        launchCoordinator.triggerVoiceFromWidget()
                    } else {
                        shareInbox.handle(url: url)
                    }
                }
        }
    }
}
