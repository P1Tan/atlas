import Foundation

struct Fact: Decodable, Identifiable, Equatable {
    let id: String
    let factText: String
    let createdAt: String

    /// Mirrors the backend's `_MAX_FACT_LENGTH` (tools.py), which PATCH
    /// /facts/{id} enforces with a 422. Checked client-side too so the edit
    /// sheet can disable Save instead of letting the user write 600
    /// characters and only then be told the server won't take them.
    static let maxTextLength = 500
}
