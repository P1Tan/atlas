import SwiftUI
import UserNotifications

@main
struct AtlasApp: App {
    @StateObject private var shareInbox = ShareInbox()
    @StateObject private var authViewModel = AuthViewModel()

    init() {
        UNUserNotificationCenter.current().delegate = NotificationPresenter.shared
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(shareInbox)
                .environmentObject(authViewModel)
                .task {
                    await authViewModel.bootstrap()
                }
                .onOpenURL { url in
                    if url.host == "login-callback" {
                        Task { await authViewModel.handle(url: url) }
                    } else {
                        shareInbox.handle(url: url)
                    }
                }
        }
    }
}
