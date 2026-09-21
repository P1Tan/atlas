import Foundation

/// Parses an ISO8601 datetime string from the backend, tolerating
/// fractional seconds. Found live (bug audit): `JSONDecoder`'s built-in
/// `.iso8601` strategy and a plain `ISO8601DateFormatter()` configured with
/// only `.withInternetDateTime` (the pattern used throughout this codebase)
/// both reject a string that includes fractional seconds (e.g.
/// "2026-08-18T20:00:00.123456-04:00"). Not currently reachable -- the
/// backend never emits fractional seconds anywhere in the date-resolution
/// path today -- but a future change that did would otherwise fail with a
/// generic decode error that doesn't point at the date at all. Tries the
/// strict, no-fractional-seconds format first (the common, current case)
/// before falling back.
enum ISO8601Parsing {
    private static let standard: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let withFractionalSeconds: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    static func date(from string: String) -> Date? {
        standard.date(from: string) ?? withFractionalSeconds.date(from: string)
    }
}

enum Confidence: String, Codable {
    case high, medium, low
}

struct ExtractedEvent: Identifiable, Codable {
    var id: String { title + datePhrase + sourceExcerpt }

    let title: String
    let datePhrase: String
    let resolvedStart: Date?
    let resolvedEnd: Date?
    let allDay: Bool
    let location: String?
    let notes: String?
    let sourceExcerpt: String
    let confidence: Confidence
    let ambiguities: [String]
    let needsConfirmation: Bool
}

struct ExtractRequest: Encodable {
    let text: String
    let referenceDatetime: String
    let timezone: String
}

struct GmailCandidate: Decodable {
    let messageId: String
    let subject: String
    let events: [ExtractedEvent]
}

enum CalendarWriteStatus: Equatable {
    case notAdded
    case adding
    case added
    /// The duplicate guard found this event already on the calendar, so
    /// nothing was written. Distinct from `.added` on purpose: the user did
    /// not just create anything, and telling them they did would invite a
    /// hunt for a second copy that doesn't exist.
    case alreadyOnCalendar
    case failed(String)
}

/// A user-editable, in-progress event derived from an `ExtractedEvent`.
/// `id` is a stable UUID independent of the (mutable) content, so editing a
/// field doesn't change the row's identity mid-edit.
struct DraftEvent: Identifiable {
    let id = UUID()

    var title: String
    /// Found live: editing "Starts" had no effect on "Ends" at all -- `end`
    /// was only ever computed once, in `init` below (the backend's date
    /// resolver never actually produces a `resolvedEnd`, confirmed in
    /// `date_resolution.py`). A user who moved the start later than the
    /// already-fixed end could reach `CalendarWriter.write()` with an
    /// inverted interval. Shifting `end` by the same delta whenever `start`
    /// changes preserves whatever duration was already set (matches how
    /// every calendar app's own start-date editing behaves), rather than
    /// leaving `end` stale.
    var start: Date {
        didSet {
            end = end.addingTimeInterval(start.timeIntervalSince(oldValue))
        }
    }
    var hasEnd: Bool
    var end: Date
    var allDay: Bool
    var location: String
    var notes: String
    var writeStatus: CalendarWriteStatus = .notAdded

    // Context from extraction, shown but not directly user-editable.
    let datePhrase: String
    let sourceExcerpt: String
    let confidence: Confidence
    let ambiguities: [String]
    /// True when the backend couldn't resolve a start date at all, so the
    /// date field is prefilled with a guess the user must actually check.
    let dateNeedsAttention: Bool
    /// The source email's subject, when this draft came from Gmail rather
    /// than a paste/share -- nil in the paste/share case.
    let sourceSubject: String?

    init(from event: ExtractedEvent, fallbackStart: Date, sourceSubject: String? = nil) {
        title = event.title
        start = event.resolvedStart ?? fallbackStart
        hasEnd = event.resolvedEnd != nil
        end = event.resolvedEnd ?? (event.resolvedStart ?? fallbackStart).addingTimeInterval(3600)
        allDay = event.allDay
        location = event.location ?? ""
        notes = event.notes ?? ""

        datePhrase = event.datePhrase
        sourceExcerpt = event.sourceExcerpt
        confidence = event.confidence
        ambiguities = event.ambiguities
        dateNeedsAttention = event.resolvedStart == nil
        self.sourceSubject = sourceSubject
    }
}
