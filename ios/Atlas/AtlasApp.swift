import SwiftUI
import UserNotifications

@main
struct AtlasApp: App {
    @StateObject private var shareInbox = ShareInbox()

    init() {
        UNUserNotificationCenter.current().delegate = NotificationPresenter.shared
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(shareInbox)
                .onOpenURL { url in
                    shareInbox.handle(url: url)
                }
        }
    }
}
