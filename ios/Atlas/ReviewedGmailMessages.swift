import Foundation

/// Remembers which Gmail messages Atlas has already shown the user, so a
/// second "Check Gmail" doesn't re-offer the same mail (and, with it, the
/// same calendar events) all over again.
///
/// This lives on the device rather than on the backend deliberately: Atlas
/// only ever holds Gmail with a readonly scope, so marking mail read or
/// adding a label -- the obvious server-side ways to track this -- are both
/// off the table (the email-privacy invariant: Atlas never modifies the
/// user's mailbox). The backend also stores Gmail credentials as a single
/// local file with no per-user table to hang this state off, so the client
/// is the only place that can own it; the ids are sent back as
/// `exclude_message_ids` so the backend skips both the fetch and the
/// per-message LLM extraction for mail already reviewed.
///
/// Only opaque Gmail message ids are stored -- no subjects, senders or
/// bodies.
enum ReviewedGmailMessages {
    private static let storageKey = "reviewedGmailMessageIds"

    /// Ordered most-recent-LAST, and capped, because the exclusion list is
    /// sent on every check and the backend caps it at 200 entries: when the
    /// cap bites we want to keep the newest ids, since older unread mail
    /// ages out of the backend's own `newer_than:30d` window anyway.
    private static let maxStored = 500

    private static var defaults: UserDefaults { .standard }

    /// The most recent `limit` reviewed ids, newest last.
    static func ids(limit: Int) -> [String] {
        guard limit > 0 else { return [] }
        let stored = storedIds()
        return Array(stored.suffix(limit))
    }

    /// Records `ids` as reviewed. Ids already known are moved to the end
    /// (they were just shown again, so they're the most recent thing the
    /// user has seen) rather than duplicated.
    static func markReviewed(_ ids: [String]) {
        let incoming = ids.filter { !$0.isEmpty }
        guard !incoming.isEmpty else { return }

        let incomingSet = Set(incoming)
        var merged = storedIds().filter { !incomingSet.contains($0) }
        // De-duplicate within the incoming batch too, keeping first order.
        var seen = Set<String>()
        merged.append(contentsOf: incoming.filter { seen.insert($0).inserted })

        if merged.count > maxStored {
            merged.removeFirst(merged.count - maxStored)
        }
        defaults.set(merged, forKey: storageKey)
    }

    /// Forgets everything, so the next check offers previously reviewed
    /// mail again -- what the "Show them again" affordance is built on.
    static func reset() {
        defaults.removeObject(forKey: storageKey)
    }

    /// `array(forKey:)` returns `[Any]?`, and nothing guarantees the value
    /// under this key is the array of strings we wrote (a botched migration
    /// or a hand-edited defaults plist is enough), so anything that isn't a
    /// string is dropped rather than trusted -- a malformed value degrades
    /// into "nothing reviewed yet", which is only ever a duplicate offer,
    /// never a crash.
    private static func storedIds() -> [String] {
        guard let raw = defaults.array(forKey: storageKey) else { return [] }
        return raw.compactMap { $0 as? String }
    }
}
