import Foundation

enum ChatRole: String, Codable {
    case system, user, assistant, tool
}

struct ChatMessage: Codable, Equatable {
    var role: ChatRole
    var content: String?
}

struct ChatRequest: Encodable {
    let messages: [ChatMessage]
    let referenceDatetime: String
    let timezone: String
}

struct ChatResponse: Decodable {
    let newMessages: [ChatMessage]
}
