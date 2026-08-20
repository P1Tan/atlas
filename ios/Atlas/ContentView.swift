import SwiftUI

struct ContentView: View {
    @StateObject private var healthChecker = HealthChecker()

    var body: some View {
        NavigationStack {
            PasteInputView()
                .navigationTitle("Atlas")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        BackendStatusIndicator(status: healthChecker.status) {
                            Task { await healthChecker.check() }
                        }
                    }
                }
        }
        .task {
            await healthChecker.check()
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
}
