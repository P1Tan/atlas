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
