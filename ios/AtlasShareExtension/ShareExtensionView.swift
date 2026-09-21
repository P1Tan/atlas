import SwiftUI

/// Found live (bug audit): `ShareViewController` used to discard the
/// `Bool` success flag from `extensionContext?.open(url)`, so a failed
/// handoff (host app refused to launch, url malformed, etc.) closed the
/// extension silently -- the shared text just vanished with nothing shown.
/// This is the shared mutable state that lets the controller (a
/// `UIViewController`, not a SwiftUI view, so no `@State` of its own) push
/// an error into the already-presented `ShareExtensionView` after an async
/// failure, instead of only being able to set up the view once at
/// creation time.
final class ShareExtensionState: ObservableObject {
    @Published var errorMessage: String?
}

struct ShareExtensionView: View {
    let sharedText: String
    @ObservedObject var state: ShareExtensionState
    let onOpen: () -> Void
    let onCancel: () -> Void

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Send this to Atlas to extract calendar events?")
                    .font(.headline)

                if let errorMessage = state.errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(.red)
                }

                ScrollView {
                    Text(sharedText.isEmpty ? "(No text found in what was shared.)" : sharedText)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 240)

                Spacer()
            }
            .padding()
            .navigationTitle("Atlas")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel", action: onCancel)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Open in Atlas", action: onOpen)
                        .disabled(sharedText.isEmpty)
                }
            }
        }
    }
}
