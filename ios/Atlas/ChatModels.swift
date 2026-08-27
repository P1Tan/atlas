import Foundation

enum ChatRole: String, Codable {
    case system, user, assistant, tool
}

/// A minimal recursive JSON value -- used only to round-trip `tool_calls`
/// byte-for-byte back to the server on the next turn. The client never
/// needs to interpret its contents, only preserve them.
enum JSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

struct ChatMessage: Codable, Equatable {
    var role: ChatRole
    var content: String?
    // Only ever populated by the server (assistant tool-call messages and
    // their tool-result replies); the client just needs to preserve these
    // faithfully when resending history for the next turn.
    var toolCallId: String?
    var toolCalls: [[String: JSONValue]]?
    var name: String?
}

struct ChatRequest: Encodable {
    let messages: [ChatMessage]
    let referenceDatetime: String
    let timezone: String
}

struct ChatResponse: Decodable {
    let newMessages: [ChatMessage]
}
