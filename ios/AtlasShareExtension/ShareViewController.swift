import SwiftUI
import UIKit
import UniformTypeIdentifiers

final class ShareViewController: UIViewController {
    private let state = ShareExtensionState()

    override func viewDidLoad() {
        super.viewDidLoad()
        loadSharedText { [weak self] text in
            DispatchQueue.main.async {
                self?.presentShareUI(text: text ?? "")
            }
        }
    }

    private func loadSharedText(completion: @escaping (String?) -> Void) {
        guard
            let item = extensionContext?.inputItems.first as? NSExtensionItem,
            let provider = item.attachments?.first
        else {
            completion(nil)
            return
        }

        let typeIdentifier: String
        if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
            typeIdentifier = UTType.plainText.identifier
        } else if provider.hasItemConformingToTypeIdentifier(UTType.text.identifier) {
            typeIdentifier = UTType.text.identifier
        } else {
            completion(nil)
            return
        }

        provider.loadItem(forTypeIdentifier: typeIdentifier) { [weak self] data, error in
            // Found live: the error and a type mismatch (some providers
            // hand back a file URL or raw Data for "plain text" rather than
            // an actual String) both used to collapse into the exact same
            // "(No text found in what was shared.)" UI as a genuinely empty
            // share -- a real provider failure was indistinguishable from
            // the user having shared nothing.
            if let error {
                DispatchQueue.main.async {
                    self?.state.errorMessage = "Couldn't read what was shared: \(error.localizedDescription)"
                }
                completion(nil)
                return
            }
            switch data {
            case let text as String:
                completion(text)
            case let data as Data:
                completion(String(data: data, encoding: .utf8))
            case let url as URL:
                // Found by review: this read used to be a bare
                // `try? String(contentsOf:)`. A file URL vended to an app
                // extension is generally security-scoped -- the extension's
                // sandbox is only granted access to it between
                // `startAccessingSecurityScopedResource()` and its matching
                // stop -- so without the scope the read can simply fail,
                // and `try?` collapsed that failure back into the generic
                // "(No text found in what was shared.)" screen: precisely
                // the "a real failure looks exactly like an empty share"
                // problem the error handling just above was added to remove.
                //
                // A `false` return is NOT a failure and deliberately isn't
                // treated as one: URLs that aren't security-scoped (e.g. one
                // already inside this extension's own container) are
                // readable without it and legitimately return false, so the
                // read is attempted either way. The stop call is balanced
                // only when the start actually succeeded -- unbalanced stops
                // are what revoke access early.
                let hasScopedAccess = url.startAccessingSecurityScopedResource()
                defer {
                    if hasScopedAccess {
                        url.stopAccessingSecurityScopedResource()
                    }
                }
                do {
                    completion(try String(contentsOf: url, encoding: .utf8))
                } catch {
                    DispatchQueue.main.async {
                        self?.state.errorMessage =
                            "Couldn't read the shared file: \(error.localizedDescription)"
                    }
                    completion(nil)
                }
            default:
                if data != nil {
                    DispatchQueue.main.async {
                        self?.state.errorMessage = "What was shared wasn't in a format Atlas could read."
                    }
                }
                completion(nil)
            }
        }
    }

    private func presentShareUI(text: String) {
        let shareView = ShareExtensionView(
            sharedText: text,
            state: state,
            onOpen: { [weak self] in self?.openInAtlas(text: text) },
            onCancel: { [weak self] in
                self?.extensionContext?.cancelRequest(
                    withError: NSError(domain: "com.p1tan.atlas.share", code: 0)
                )
            }
        )
        let hosting = UIHostingController(rootView: shareView)
        addChild(hosting)
        hosting.view.frame = view.bounds
        hosting.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(hosting.view)
        hosting.didMove(toParent: self)
    }

    /// Hands the shared text off to the main app via a custom URL scheme and
    /// ends the extension. The extension itself never touches /extract --
    /// the app owns that flow so there's exactly one implementation of it.
    private func openInAtlas(text: String) {
        // Found live: manual `"atlas://extract?text=\(encoded)"` string
        // interpolation with `.urlQueryAllowed` percent-encoding does NOT
        // escape "&" (or "+"/"="), which are valid, unreserved characters
        // in a URL query component on their own -- but a literal "&" inside
        // the VALUE is indistinguishable from a real query-item delimiter
        // once substituted into the string, so any shared text containing
        // one ("Bob & Alice", "Terms & Conditions") was silently truncated
        // right at that point when ShareInbox.handle(url:) parsed it back
        // out via URLComponents on the other end. Building the URL through
        // URLComponents/URLQueryItem here instead of interpolating a
        // pre-encoded string is what actually escapes delimiter characters
        // inside the value correctly.
        var components = URLComponents()
        components.scheme = "atlas"
        components.host = "extract"
        components.queryItems = [URLQueryItem(name: "text", value: text)]

        guard let url = components.url else {
            state.errorMessage = "Couldn't prepare this for Atlas. Please copy the text and paste it into the app instead."
            return
        }

        extensionContext?.open(url) { [weak self] success in
            guard let self else { return }
            if success {
                self.extensionContext?.completeRequest(returningItems: nil)
            } else {
                // Found live: this Bool was previously discarded entirely,
                // so a failed handoff (host app refused to launch, etc.)
                // closed the extension with the shared text gone and no
                // indication anything went wrong. Left open on failure
                // (not completeRequest) so the user can see the error and
                // try "Open in Atlas" again rather than losing the share.
                DispatchQueue.main.async {
                    self.state.errorMessage = "Couldn't open Atlas. Make sure the app is installed, then try again."
                }
            }
        }
    }
}
