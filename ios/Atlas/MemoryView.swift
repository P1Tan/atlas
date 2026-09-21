import SwiftUI

struct MemoryView: View {
    @EnvironmentObject private var authViewModel: AuthViewModel
    @StateObject private var viewModel = MemoryViewModel()
    @State private var editingFact: Fact?

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                List {
                    ForEach(viewModel.facts) { fact in
                        // A tap gesture on the row's own Text rather than a
                        // Button label: a Button would swallow the row into
                        // its own accessibility element and take
                        // "MemoryFactRow" off the static text the list (and
                        // its tests) matches on. contentShape makes the whole
                        // row width tappable, not just the glyphs; List keeps
                        // owning the horizontal swipe, so delete still works.
                        Text(fact.factText)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                            .onTapGesture { editingFact = fact }
                            .accessibilityIdentifier("MemoryFactRow")
                    }
                    .onDelete { offsets in
                        deleteFacts(at: offsets)
                    }
                }
                .refreshable {
                    await viewModel.load(accessToken: await authViewModel.currentAccessToken())
                }

                if viewModel.isLoading && viewModel.facts.isEmpty {
                    ProgressView()
                } else if viewModel.facts.isEmpty {
                    Text("No memories yet. Tell Atlas something to remember in chat.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                        .accessibilityIdentifier("MemoryEmptyState")
                }
            }

            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .font(.footnote)
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                    .accessibilityIdentifier("MemoryErrorMessage")
            }
        }
        .task {
            await viewModel.load(accessToken: await authViewModel.currentAccessToken())
        }
        .sheet(item: $editingFact) { fact in
            MemoryEditSheet(fact: fact) { newText in
                Task {
                    await viewModel.update(
                        fact, newText: newText, accessToken: await authViewModel.currentAccessToken()
                    )
                }
            }
        }
    }

    private func deleteFacts(at offsets: IndexSet) {
        let factsToDelete = offsets.map { viewModel.facts[$0] }
        Task {
            let accessToken = await authViewModel.currentAccessToken()
            for fact in factsToDelete {
                await viewModel.delete(fact, accessToken: accessToken)
            }
        }
    }
}

/// Edits one remembered fact. Sheet rather than a pushed screen: this is a
/// single short field with an obvious commit/abandon pair, and the Memory tab
/// has no other navigation to preserve underneath it.
private struct MemoryEditSheet: View {
    let fact: Fact
    let onSave: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var text: String
    @FocusState private var isTextFocused: Bool

    init(fact: Fact, onSave: @escaping (String) -> Void) {
        self.fact = fact
        self.onSave = onSave
        _text = State(initialValue: fact.factText)
    }

    private var trimmedText: String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The same two conditions the backend answers 422 for -- checked here so
    /// Save is simply unavailable instead of failing after a round trip.
    private var canSave: Bool {
        !trimmedText.isEmpty && trimmedText.count <= Fact.maxTextLength
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 8) {
                // TextEditor, not TextField: remembered facts are sentences
                // and some run several lines, which a single-line field would
                // hide the middle of while editing.
                TextEditor(text: $text)
                    .frame(minHeight: 120, maxHeight: 220)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color.secondary.opacity(0.3))
                    )
                    .focused($isTextFocused)
                    .accessibilityIdentifier("MemoryEditTextField")

                // Counts the trimmed text, since that is what actually gets
                // sent and length-checked -- a counter that disagreed with
                // the disabled Save button would just look broken.
                Text("\(trimmedText.count)/\(Fact.maxTextLength)")
                    .font(.caption)
                    .foregroundStyle(trimmedText.count > Fact.maxTextLength ? Color.red : Color.secondary)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                    .accessibilityIdentifier("MemoryEditCharacterCount")

                if trimmedText.isEmpty {
                    Text("A memory can't be empty.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if trimmedText.count > Fact.maxTextLength {
                    Text("Too long by \(trimmedText.count - Fact.maxTextLength) characters.")
                        .font(.caption)
                        .foregroundStyle(.red)
                }

                Spacer()
            }
            .padding()
            .navigationTitle("Edit Memory")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .accessibilityIdentifier("MemoryEditCancelButton")
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        onSave(trimmedText)
                        dismiss()
                    }
                    .disabled(!canSave)
                    .accessibilityIdentifier("MemoryEditSaveButton")
                }
            }
        }
        .task {
            // Straight into editing -- the sheet exists for no other reason,
            // and an extra tap to raise the keyboard is pure friction.
            isTextFocused = true
        }
    }
}

#Preview {
    NavigationStack {
        MemoryView()
    }
    .environmentObject(AuthViewModel())
}
