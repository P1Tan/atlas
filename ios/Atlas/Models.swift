import Foundation

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

/// A user-editable, in-progress event derived from an `ExtractedEvent`.
/// `id` is a stable UUID independent of the (mutable) content, so editing a
/// field doesn't change the row's identity mid-edit.
struct DraftEvent: Identifiable {
    let id = UUID()

    var title: String
    var start: Date
    var hasEnd: Bool
    var end: Date
    var allDay: Bool
    var location: String
    var notes: String

    // Context from extraction, shown but not directly user-editable.
    let datePhrase: String
    let sourceExcerpt: String
    let confidence: Confidence
    let ambiguities: [String]
    /// True when the backend couldn't resolve a start date at all, so the
    /// date field is prefilled with a guess the user must actually check.
    let dateNeedsAttention: Bool

    init(from event: ExtractedEvent, fallbackStart: Date) {
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
    }
}
