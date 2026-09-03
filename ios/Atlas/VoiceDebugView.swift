import SwiftUI

#if DEBUG
/// Milestone 7.2b manual/UI-test harness for the on-device STT -> LiveKit
/// data-channel path -- deliberately not polished product UI (real voice UX
/// is Milestone 7.4). Gives a start/stop button and a visible transcript so
/// the wiring (permissions -> token -> room connect -> transcription ->
/// data publish) can be exercised by hand or by `VoiceDebugUITests`.
/// `#if DEBUG`-gated end to end (this file, its `ContentView` tab, and the
/// `AtlasTab` case) so none of it ships in a Release build.
struct VoiceDebugView: View {
    @EnvironmentObject private var authViewModel: AuthViewModel
    @StateObject private var viewModel = VoiceSessionViewModel()

    var body: some View {
        VStack(spacing: 16) {
            Text(stateDescription)
                .font(.headline)
                .accessibilityIdentifier("VoiceDebugStateLabel")

            ScrollView {
                Text(viewModel.lastTranscript.isEmpty ? "No transcript yet." : viewModel.lastTranscript)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
            .frame(maxHeight: 200)
            .background(Color(.secondarySystemBackground))
            .accessibilityIdentifier("VoiceDebugTranscriptText")

            HStack(spacing: 16) {
                Button("Start") {
                    Task {
                        await viewModel.start(accessToken: await authViewModel.currentAccessToken())
                    }
                }
                .disabled(viewModel.state == .connecting || viewModel.state == .listening)
                .accessibilityIdentifier("VoiceDebugStartButton")

                Button("Stop") {
                    Task { await viewModel.stop() }
                }
                .disabled(viewModel.state != .listening && viewModel.state != .connecting)
                .accessibilityIdentifier("VoiceDebugStopButton")
            }

            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .font(.footnote)
                    .padding(.horizontal)
                    .accessibilityIdentifier("VoiceDebugErrorMessage")
            }

            Spacer()
        }
        .padding()
    }

    private var stateDescription: String {
        switch viewModel.state {
        case .idle: return "Not listening"
        case .connecting: return "Connecting..."
        case .listening: return "Listening..."
        case .stopped: return "Stopped"
        }
    }
}

#Preview {
    VoiceDebugView()
        .environmentObject(AuthViewModel())
}
#endif
