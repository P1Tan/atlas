import SwiftUI

struct ContentView: View {
    @StateObject private var healthChecker = HealthChecker()

    var body: some View {
        VStack(spacing: 16) {
            Text("Atlas")
                .font(.largeTitle.bold())

            statusView

            Button("Retry") {
                Task { await healthChecker.check() }
            }
        }
        .padding()
        .task {
            await healthChecker.check()
        }
    }

    @ViewBuilder
    private var statusView: some View {
        switch healthChecker.status {
        case .loading:
            ProgressView("Checking backend…")
        case .ok(let status):
            Label("Backend: \(status)", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed(let message):
            Label("Backend unreachable: \(message)", systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red)
        }
    }
}

#Preview {
    ContentView()
}
