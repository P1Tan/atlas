import Foundation

/// Receives shared text handed off from AtlasShareExtension via the
/// atlas://extract?text=... URL scheme. PasteInputView consumes and clears
/// pendingText, then runs it through the normal /extract flow -- there is no
/// separate extraction path for shared content.
@MainActor
final class ShareInbox: ObservableObject {
    @Published var pendingText: String?

    func handle(url: URL) {
        guard
            url.scheme == "atlas",
            url.host == "extract",
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let text = components.queryItems?.first(where: { $0.name == "text" })?.value,
            !text.isEmpty
        else { return }

        pendingText = text
    }
}
