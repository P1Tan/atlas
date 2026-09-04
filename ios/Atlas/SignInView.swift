import SwiftUI

struct SignInView: View {
    @EnvironmentObject private var authViewModel: AuthViewModel
    @State private var email: String = ""
    @FocusState private var isEmailFocused: Bool

    var body: some View {
        VStack(spacing: 20) {
            Spacer()

            Text("Atlas")
                .font(.largeTitle.bold())

            if authViewModel.linkSent {
                VStack(spacing: 8) {
                    Image(systemName: "envelope.badge.fill")
                        .font(.largeTitle)
                        .foregroundStyle(.secondary)
                    Text("Check your email")
                        .font(.headline)
                    Text("We sent a sign-in link to \(email). Tap it to continue.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .accessibilityIdentifier("MagicLinkSentMessage")
                    Button("Use a different email") {
                        authViewModel.resetLinkSent()
                    }
                    .font(.footnote)
                    .accessibilityIdentifier("UseDifferentEmailButton")
                }
                .padding(.horizontal)
            } else {
                Text("Sign in to sync your memory and history.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)

                TextField("Email", text: $email)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($isEmailFocused)
                    .padding(.horizontal)
                    .accessibilityIdentifier("SignInEmailField")

                Button {
                    isEmailFocused = false
                    Task { await authViewModel.sendMagicLink(to: email) }
                } label: {
                    if authViewModel.isSendingLink {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Text("Send Sign-In Link")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || authViewModel.isSendingLink
                )
                .padding(.horizontal)
                .accessibilityIdentifier("SendMagicLinkButton")
            }

            if let errorMessage = authViewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
                    .accessibilityIdentifier("SignInErrorMessage")
            }

            // Milestone 9.2 (NFR4 / spec §14): a persistent, always-visible
            // disclosure -- not a one-time onboarding modal a user taps past
            // and never sees again -- mirroring the same caption pattern
            // PasteInputView already uses for its Gmail-specific disclosure
            // (Milestone 3.3). This is the general one, since it applies to
            // every message/utterance, not one connected feature.
            //
            // Security review finding: an earlier draft's sentence structure
            // scoped OpenAI's role narrowly to "generate responses," which
            // could read as excluding remembered facts -- but remember_fact/
            // search_facts also send fact text to OpenAI's embeddings API,
            // both when a memory is stored and on every later recall.
            // Reworded so "messages and memories" are grouped as one clause
            // OpenAI processes, distinct from Cartesia's narrower TTS-only
            // role. "Encrypted at rest" (not just "encrypted") deliberately
            // uses the same precise term the spec's own NFR4 does -- true of
            // Supabase's storage layer, not a claim that Atlas's own backend
            // can't read stored facts (it has to, to search/display them).
            Text(
                "Your messages and memories are processed by OpenAI, and voice replies are spoken by "
                    + "Cartesia. Voice is transcribed on your device -- only the text is sent. Stored "
                    + "memories are encrypted at rest and yours to view or delete anytime."
            )
            .font(.caption2)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .padding(.horizontal)
            .accessibilityIdentifier("DataUsageDisclosure")

            Spacer()
            Spacer()
        }
        .padding()
    }
}

#Preview {
    SignInView()
        .environmentObject(AuthViewModel())
}
