import CoreLocation
import Foundation

/// Fetches the user's current approximate location, once, as a
/// human-readable "City, Region" string -- not for anything that needs
/// precision or continuous tracking, just enough context for the model to
/// answer "what's the weather like" without the user having to name a
/// place. `nil` on denied permission or any failure along the way (no
/// location fix, reverse-geocoding failure) -- treated as "no location
/// available," not an error callers need to surface; the model just won't
/// have it as context, same as before this existed.
///
/// `CLLocationManager`'s delegate-based API has no native async surface, so
/// this bridges it with `CheckedContinuation` -- one for the authorization
/// prompt, one for the actual location fix.
///
/// Repeat calls within `cacheLifetime` are answered from the last
/// successful result rather than re-running that whole round trip -- see
/// `cachedDescription`.
@MainActor
final class LocationProvider: NSObject {
    private let manager = CLLocationManager()
    /// Arrays, not single optionals -- found live: `send()` (text) and
    /// `startVoiceTurn()` (voice, including the automatic continuous-mode
    /// restart) can both call `currentLocationDescription()` concurrently
    /// (e.g. a text message sent while a voice turn's location lookup is
    /// still in flight -- a GPS fix + reverse-geocode routinely takes a
    /// couple of seconds). A single stored continuation meant the second
    /// caller's `withCheckedContinuation` silently overwrote the first's;
    /// when the delegate callback fired, only the second continuation ever
    /// resumed, and the first caller's `await` hung forever -- along with
    /// whatever flag its own caller was holding for the duration (e.g.
    /// `ChatViewModel.isStartingVoiceTurn`), permanently disabling the mic
    /// with no error. All concurrent callers legitimately want the SAME
    /// answer from ONE underlying CoreLocation request, so this now fans
    /// the result out to every waiter instead of only the most recent one.
    private var authContinuations: [CheckedContinuation<CLAuthorizationStatus, Never>] = []
    private var locationContinuations: [CheckedContinuation<CLLocation?, Never>] = []

    /// Found by review: `currentLocationDescription()` did the full GPS fix
    /// + `CLGeocoder.reverseGeocodeLocation` round trip on EVERY call, and
    /// continuous-conversation mode calls it once per turn (each automatic
    /// restart in `ChatViewModel.onReplyCompleted` goes through
    /// `startVoiceTurn`). Two costs, both real: 1-3s of dead air added
    /// before listening resumes on every single turn, and a burst of
    /// reverse-geocode requests against an API Apple explicitly documents
    /// as rate-limited -- and a throttled geocode fails, which this class
    /// (correctly, for its contract) turns into a silent `nil`, so the
    /// model would just quietly stop having location context partway
    /// through a conversation.
    ///
    /// A coarse "City, Region" string is stable over far longer than one
    /// conversation, so the last successful answer is reused for
    /// `cacheLifetime`. Deliberately caches only SUCCESS: a `nil` means
    /// permission was denied or a fix/geocode failed, and caching that
    /// would keep a just-granted permission (or a transient failure) from
    /// taking effect for the next five minutes, for no saved work worth
    /// having.
    ///
    /// Orthogonal to the continuation fan-out above, which de-duplicates
    /// concurrent *in-flight* requests; this de-duplicates *sequential*
    /// ones. Both are needed -- neither covers the other's case.
    private var cachedDescription: String?
    private var cachedDescriptionTime: Date?
    private let cacheLifetime: TimeInterval = 5 * 60

    override init() {
        super.init()
        manager.delegate = self
    }

    func currentLocationDescription() async -> String? {
        if let cachedDescription, let cachedAt = cachedDescriptionTime,
            Date().timeIntervalSince(cachedAt) < cacheLifetime
        {
            return cachedDescription
        }

        let status = await resolvedAuthorizationStatus()
        guard status == .authorizedWhenInUse || status == .authorizedAlways else { return nil }

        guard let location = await requestOneLocation() else { return nil }

        let geocoder = CLGeocoder()
        guard let placemark = try? await geocoder.reverseGeocodeLocation(location).first else { return nil }

        let description: String?
        switch (placemark.locality, placemark.administrativeArea) {
        case let (city?, region?): description = "\(city), \(region)"
        case let (city?, nil): description = city
        case let (nil, region?): description = region
        case (nil, nil): description = nil
        }

        // See `cachedDescription`: only a real answer is worth remembering.
        if let description {
            cachedDescription = description
            cachedDescriptionTime = Date()
        }
        return description
    }

    /// If permission hasn't been decided yet, prompts and waits for the
    /// user's answer; otherwise returns the current status immediately with
    /// no prompt (a denied/restricted status is a normal, silent no-op
    /// here, not re-prompted every turn).
    private func resolvedAuthorizationStatus() async -> CLAuthorizationStatus {
        let current = manager.authorizationStatus
        guard current == .notDetermined else { return current }
        return await withCheckedContinuation { continuation in
            let alreadyInFlight = !authContinuations.isEmpty
            authContinuations.append(continuation)
            guard !alreadyInFlight else { return }
            manager.requestWhenInUseAuthorization()
        }
    }

    private func requestOneLocation() async -> CLLocation? {
        await withCheckedContinuation { continuation in
            let alreadyInFlight = !locationContinuations.isEmpty
            locationContinuations.append(continuation)
            guard !alreadyInFlight else { return }
            manager.requestLocation()
        }
    }
}

extension LocationProvider: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            let waiters = self.authContinuations
            self.authContinuations.removeAll()
            for continuation in waiters {
                continuation.resume(returning: status)
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        let result = locations.last
        Task { @MainActor in
            let waiters = self.locationContinuations
            self.locationContinuations.removeAll()
            for continuation in waiters {
                continuation.resume(returning: result)
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in
            let waiters = self.locationContinuations
            self.locationContinuations.removeAll()
            for continuation in waiters {
                continuation.resume(returning: nil)
            }
        }
    }
}
