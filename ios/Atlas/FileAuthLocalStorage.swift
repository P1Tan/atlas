import Foundation
import Supabase

/// A file-backed `AuthLocalStorage`, used instead of the Supabase SDK's
/// default `KeychainLocalStorage`.
///
/// This project builds with code signing disabled (`CODE_SIGNING_ALLOWED: NO`
/// in `project.yml`, so Simulator development doesn't need an Apple
/// Developer account). Keychain writes require a valid code signature;
/// without one, `SecItemAdd` fails, and the SDK's internal `SessionStorage`
/// wrapper swallows that failure and only logs it (see
/// `SessionStorage.store` in the SDK source) -- so `setSession()` reports
/// success while nothing is ever actually persisted, and every later read
/// throws `sessionMissing`. Confirmed by direct diagnostic: `setSession`
/// returned a valid session, but `currentSession` was `nil` immediately
/// after, with no thrown error anywhere in the app's own code.
///
/// Stores the session as a plain file in the app's sandboxed Application
/// Support directory instead -- not encrypted at rest the way Keychain is,
/// an accepted trade-off for local development on a personal,
/// non-distributed app. If code signing is ever enabled (e.g. for TestFlight
/// or App Store distribution), switch back to `KeychainLocalStorage()`,
/// which will work correctly once the app has a real signing identity.
struct FileAuthLocalStorage: AuthLocalStorage {
    private let directory: URL

    init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        directory = base.appendingPathComponent("AtlasAuth", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    private func url(forKey key: String) -> URL {
        directory.appendingPathComponent(key)
    }

    func store(key: String, value: Data) throws {
        try value.write(to: url(forKey: key), options: .atomic)
    }

    func retrieve(key: String) throws -> Data? {
        let fileURL = url(forKey: key)
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        return try Data(contentsOf: fileURL)
    }

    func remove(key: String) throws {
        let fileURL = url(forKey: key)
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
        try FileManager.default.removeItem(at: fileURL)
    }
}
