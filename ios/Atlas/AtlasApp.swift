import SwiftUI

@main
struct AtlasApp: App {
    @StateObject private var shareInbox = ShareInbox()

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
