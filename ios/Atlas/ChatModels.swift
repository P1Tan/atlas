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

/// The result shape `set_reminder` returns, whether it ran during a
/// text-chat turn (as a `ChatMessage(role: .tool, ...)`'s JSON `content`) or
/// a voice turn (as a `ToolResultMessage`'s `result`, Milestone 7.4a-2) --
/// the backend uses the exact same tool-result payload either way, so both
/// paths decode into this one shared struct rather than duplicating it.
struct SetReminderToolResult: Decodable {
    let ok: Bool
    let title: String?
    let triggerTime: String?
    let reason: String?
}

/// Backend -> iOS voice message (Milestone 7.4a): the assistant's full reply
/// text for the current voice turn, sent as a LiveKit data message on the
/// voice room. Decoded by `VoiceSessionController`.
struct AssistantReplyMessage: Decodable {
    let type: String
    let text: String
}

/// Backend -> iOS voice message (Milestone 7.4a-2): the outcome of a tool
/// call made during a voice turn, sent as a LiveKit data message on the
/// voice room. Decoded by `VoiceSessionController`.
struct ToolResultMessage: Decodable {
    let type: String
    let name: String
    let result: SetReminderToolResult
}

/// Backend -> iOS voice message (Milestone 9.1, NFR2 reliability): sent when
/// the voice pipeline hits an error it can't recover from on its own (e.g.
/// the LLM/TTS provider call itself fails -- not a tool call failing, which
/// the model already explains gracefully in a normal `assistant_reply`).
/// `message` is a fixed, generic, non-leaky string -- never the raw
/// exception text, which could contain internal provider/error detail.
/// Decoded by `VoiceSessionController`.
///
/// No UI-test coverage for this decode path, matching the rest of
/// `VoiceSessionController` (no unit-test target exists for it): reaching
/// `.awaitingReply` at all needs a real voice turn, which needs real mic
/// input the Simulator can't provide (assistant-spec.md §18). Live-verified
/// instead against the real backend (see PROGRESS.md's 9.1 entry) --
/// intentional, not an oversight.
struct PipelineErrorMessage: Decodable {
    let type: String
    let message: String
}
