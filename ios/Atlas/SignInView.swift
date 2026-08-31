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
