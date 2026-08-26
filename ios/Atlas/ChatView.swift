import SwiftUI

struct ChatView: View {
    @StateObject private var viewModel = ChatViewModel()
    @State private var inputText: String = ""
    @FocusState private var isInputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(viewModel.messages.enumerated()), id: \.offset) { index, message in
                            ChatBubble(message: message)
                                .id(index)
                        }
                        if viewModel.isSending {
                            HStack(spacing: 8) {
                                ProgressView()
                                Text("Thinking…")
                                    .foregroundStyle(.secondary)
                            }
                            .accessibilityIdentifier("ChatThinkingIndicator")
                        }
                    }
                    .padding()
                }
                .accessibilityIdentifier("ChatMessageList")
                .onChange(of: viewModel.messages.count) { _, _ in
                    guard let lastIndex = viewModel.messages.indices.last else { return }
                    withAnimation {
                        proxy.scrollTo(lastIndex, anchor: .bottom)
                    }
                }
            }

            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .font(.footnote)
                    .padding(.horizontal)
                    .accessibilityIdentifier("ChatErrorMessage")
            }

            Divider()

            HStack(alignment: .bottom, spacing: 8) {
                TextField("Message", text: $inputText, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...5)
                    .focused($isInputFocused)
                    .accessibilityIdentifier("ChatInputField")

                Button {
                    let text = inputText
                    inputText = ""
                    isInputFocused = false
                    Task { await viewModel.send(text) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isSending)
                .accessibilityIdentifier("ChatSendButton")
            }
            .padding()
        }
    }
}

private struct ChatBubble: View {
    let message: ChatMessage

    private var isUser: Bool { message.role == .user }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 40) }
            Text(message.content ?? "")
                .padding(10)
                .background(isUser ? Color.accentColor : Color.secondary.opacity(0.15))
                .foregroundStyle(isUser ? Color.white : Color.primary)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .accessibilityIdentifier(isUser ? "ChatUserMessage" : "ChatAssistantMessage")
            if !isUser { Spacer(minLength: 40) }
        }
    }
}

#Preview {
    NavigationStack {
        ChatView()
    }
}
