import SwiftUI

struct ShareExtensionView: View {
    let sharedText: String
    let onOpen: () -> Void
    let onCancel: () -> Void

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Send this to Atlas to extract calendar events?")
                    .font(.headline)

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
