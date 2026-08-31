import Foundation

struct Fact: Decodable, Identifiable, Equatable {
    let id: String
    let factText: String
    let createdAt: String
}
