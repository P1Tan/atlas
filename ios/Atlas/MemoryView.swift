import SwiftUI

struct MemoryView: View {
    @EnvironmentObject private var authViewModel: AuthViewModel
    @StateObject private var viewModel = MemoryViewModel()

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                List {
                    ForEach(viewModel.facts) { fact in
                        Text(fact.factText)
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

#Preview {
    NavigationStack {
        MemoryView()
    }
    .environmentObject(AuthViewModel())
}
