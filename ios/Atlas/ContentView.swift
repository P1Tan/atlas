import SwiftUI

private enum AtlasTab: Hashable {
    case chat, email
}

struct ContentView: View {
    @StateObject private var healthChecker = HealthChecker()
    @EnvironmentObject private var shareInbox: ShareInbox
    @State private var selectedTab: AtlasTab = .chat

    var body: some View {
        TabView(selection: $selectedTab) {
            NavigationStack {
                ChatView()
                    .navigationTitle("Atlas")
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            BackendStatusIndicator(status: healthChecker.status) {
                                Task { await healthChecker.check() }
                            }
                        }
                    }
            }
            .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
            .tag(AtlasTab.chat)

            NavigationStack {
                PasteInputView()
                    .navigationTitle("Email")
            }
            .tabItem { Label("Email", systemImage: "envelope") }
            .tag(AtlasTab.email)
        }
        .task {
            await healthChecker.check()
        }
        // A share-sheet hand-off targets the Email tab's paste box (still
        // the only place that flow lives, until Milestone 4.3 migrates
        // email-to-calendar into the chat tool loop) -- surface it, don't
        // populate a screen the user isn't looking at.
        .onChange(of: shareInbox.pendingText) { _, newValue in
            guard newValue != nil else { return }
            selectedTab = .email
        }
    }
}

private struct BackendStatusIndicator: View {
    let status: HealthStatus
    let retry: () -> Void

    var body: some View {
        Button(action: retry) {
            switch status {
            case .loading:
                ProgressView()
            case .ok:
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            case .failed:
                Image(systemName: "xmark.octagon.fill")
                    .foregroundStyle(.red)
            }
        }
        .accessibilityLabel("Backend status")
    }
}

#Preview {
    ContentView()
        .environmentObject(ShareInbox())
}
