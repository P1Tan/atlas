import SwiftUI
import UIKit
import UniformTypeIdentifiers

final class ShareViewController: UIViewController {
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

        provider.loadItem(forTypeIdentifier: typeIdentifier) { data, _ in
            completion(data as? String)
        }
    }

    private func presentShareUI(text: String) {
        let shareView = ShareExtensionView(
            sharedText: text,
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
        guard
            let encoded = text.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
            let url = URL(string: "atlas://extract?text=\(encoded)")
        else {
            extensionContext?.completeRequest(returningItems: nil)
            return
        }

        extensionContext?.open(url) { [weak self] _ in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
    }
}
