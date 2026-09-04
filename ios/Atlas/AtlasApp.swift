import SwiftUI
import UserNotifications

@main
struct AtlasApp: App {
    @StateObject private var shareInbox = ShareInbox()
    @StateObject private var authViewModel = AuthViewModel()
    @StateObject private var launchCoordinator = LaunchCoordinator()

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
