import SwiftUI

struct ChatView: View {
    @StateObject private var viewModel = ChatViewModel()
    @EnvironmentObject private var authViewModel: AuthViewModel
    @EnvironmentObject private var launchCoordinator: LaunchCoordinator
    @EnvironmentObject private var shareInbox: ShareInbox
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

            voiceStatusArea

            Divider()

            HStack(alignment: .bottom, spacing: 8) {
                TextField("Message", text: $inputText, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...5)
                    .focused($isInputFocused)
                    .accessibilityIdentifier("ChatInputField")

                Button {
                    handleMicTap()
                } label: {
                    Image(systemName: viewModel.voiceState == .listening ? "stop.circle.fill" : "mic.circle.fill")
                        .font(.title2)
                        .foregroundStyle(viewModel.voiceState == .listening ? Color.red : Color.accentColor)
                }
                .disabled(viewModel.voiceState == .thinking || viewModel.voiceState == .speaking)
                .accessibilityIdentifier("VoiceMicButton")

                Button {
                    let text = inputText
                    inputText = ""
                    isInputFocused = false
                    Task {
                        let accessToken = await authViewModel.currentAccessToken()
                        await viewModel.send(text, accessToken: accessToken)
                    }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isSending)
                .accessibilityIdentifier("ChatSendButton")
            }
            .padding()
        }
        // Milestone 8.1 (FR11, launch-to-listen): on a genuine cold open,
        // drop straight into listening rather than making the user find and
        // tap the mic button first -- reuses the exact same startVoiceTurn()
        // path the mic button itself calls, so the listening state's
        // existing "unmistakable indicator + instant cancel" UI (mic-launch
        // guard, S11) applies here automatically, no separate UI needed.
        // consumeShouldAutoStartVoice() only ever returns true once per
        // process lifetime, so this is a no-op on every later appearance of
        // this view (tab switches, foreground/background cycles, etc.).
        //
        // Chat is always the default tab, so a cold launch via the share
        // extension's atlas://extract deep link (AtlasApp's .onOpenURL ->
        // ShareInbox.handle(url:)) mounts this same view and .task before
        // ContentView's onChange(of: shareInbox.pendingText) switches to the
        // Email tab. SwiftUI doesn't guarantee .onOpenURL fires before a
        // sibling view's .task on cold launch, so a short grace window is
        // given for a racing share hand-off to arrive before treating this
        // as a genuine "opened the app to talk" launch -- auto-starting
        // voice underneath a share-to-Email hand-off would be surprising
        // and would open a LiveKit connection nobody asked for.
        .task {
            guard launchCoordinator.consumeShouldAutoStartVoice() else { return }
            try? await Task.sleep(for: .milliseconds(150))
            guard shareInbox.pendingText == nil else { return }
            let accessToken = await authViewModel.currentAccessToken()
            await viewModel.startVoiceTurn(accessToken: accessToken)
        }
        // Milestone 8.2: the widget deep-link's own trigger, distinct from
        // the cold-launch `.task` above -- this fires on EVERY widget tap,
        // including ones where the app was already running (the `.task`
        // above only ever fires once, on a genuine cold open).
        // `ChatViewModel.startVoiceTurn`'s own idle-state/reentrancy guards
        // make it safe if both happen to fire for the same cold launch.
        .onChange(of: launchCoordinator.widgetVoiceTrigger) { _, newValue in
            guard newValue != nil else { return }
            Task {
                let accessToken = await authViewModel.currentAccessToken()
                await viewModel.startVoiceTurn(accessToken: accessToken)
            }
        }
        // A widget tap that arrives before this view is mounted -- e.g.
        // during the async auth bootstrap window on a cold launch, before
        // ContentView has switched from SignInView to MainTabView -- would
        // otherwise be silently dropped: `onChange` above only fires on a
        // transition AFTER it attaches, not for whatever value is already
        // current at mount time. Mirrors the same fix PasteInputView
        // already applies for ShareInbox.pendingText's identical
        // mount-timing race (Milestone 4.2).
        .task {
            guard launchCoordinator.widgetVoiceTrigger != nil else { return }
            let accessToken = await authViewModel.currentAccessToken()
            await viewModel.startVoiceTurn(accessToken: accessToken)
        }
    }

    /// The status/controls area shown above the input bar while a voice turn
    /// is in progress -- text typing and sending stay fully available the
    /// whole time (per the spec's "text and voice turns share one
    /// conversation view"), this is purely additive.
    @ViewBuilder
    private var voiceStatusArea: some View {
        switch viewModel.voiceState {
        case .idle:
            EmptyView()
        case .listening:
            VoiceListeningBar(
                interimTranscript: viewModel.liveInterimTranscript,
                onCancel: { Task { await viewModel.cancelVoiceTurn() } }
            )
        case .thinking:
            HStack(spacing: 8) {
                ProgressView()
                Text("Atlas is thinking…")
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal)
            .padding(.top, 4)
            .accessibilityIdentifier("VoiceThinkingIndicator")
        case .speaking:
            HStack(spacing: 12) {
                Image(systemName: "waveform")
                    .foregroundStyle(Color.accentColor)
                Text("Speaking…")
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    viewModel.stopPlayback()
                } label: {
                    Image(systemName: "stop.fill")
                }
                .accessibilityIdentifier("VoiceStopPlaybackButton")

                Button {
                    viewModel.replayLastReply()
                } label: {
                    Image(systemName: "arrow.counterclockwise")
                }
                .accessibilityIdentifier("VoiceReplayButton")
            }
            .padding(.horizontal)
            .padding(.top, 4)
            .accessibilityIdentifier("VoiceSpeakingIndicator")
        }
    }

    private func handleMicTap() {
        switch viewModel.voiceState {
        case .idle:
            Task {
                let accessToken = await authViewModel.currentAccessToken()
                await viewModel.startVoiceTurn(accessToken: accessToken)
            }
        case .listening:
            Task { await viewModel.stopVoiceTurn() }
        case .thinking, .speaking:
            break
        }
    }
}

/// The "unmistakable live recording indicator" the spec calls for while
/// listening -- a pulsing dot plus the live interim transcript, and a
/// clearly separate cancel ("X") action from the mic button's own
/// tap-to-stop. Never surprises the user into thinking a tap silently did
/// nothing: something is always visibly happening the instant the mic opens.
private struct VoiceListeningBar: View {
    let interimTranscript: String?
    let onCancel: () -> Void

    @State private var isPulsing = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle()
                    .fill(Color.red)
                    .frame(width: 10, height: 10)
                    .opacity(isPulsing ? 0.35 : 1.0)
                    .animation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true), value: isPulsing)
                    .onAppear { isPulsing = true }
                    .accessibilityIdentifier("VoiceListeningIndicator")

                Text("Listening…")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Spacer()

                Button {
                    onCancel()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
                .accessibilityIdentifier("VoiceCancelButton")
            }

            Text(interimTranscript?.isEmpty == false ? interimTranscript! : " ")
                .font(.body)
                .foregroundStyle(.primary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("VoiceInterimTranscript")
        }
        .padding(.horizontal)
        .padding(.top, 4)
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
    .environmentObject(AuthViewModel())
    .environmentObject(LaunchCoordinator())
    .environmentObject(ShareInbox())
}
