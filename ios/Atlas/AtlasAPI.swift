import Foundation

/// The one place the backend's base URL lives -- previously duplicated as an
/// identical `private let baseURL = "http://127.0.0.1:8000"` in four
/// separate view models plus two more one-off literals, which is why this
/// needed changing in six places the first time real-device testing needed
/// something other than localhost.
///
/// `127.0.0.1` means "this device" -- on the Simulator that's the Mac
/// itself (Simulator shares the host's network stack), but on a real iPhone
/// it means the phone, which has no backend running on it. Point this at
/// the Mac's LAN IP (same Wi-Fi network) to test against a physical device;
/// it works unchanged for Simulator either way, since the Mac can always
/// reach its own LAN IP.
enum AtlasAPI {
    static let baseURL = "http://192.168.1.62:8000"
}
