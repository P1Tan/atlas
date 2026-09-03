import Foundation
import LiveKit

/// Milestone 7.2b: drives an on-device-STT voice session -- fetches a
/// LiveKit join token from the backend (`POST /voice/token`), joins the same
/// fixed dev room the backend voice agent joins (`app/voice_agent.py`), and
/// forwards `VoiceTranscriptionService`'s events to it as LiveKit data
/// messages, in the exact shape `LiveKitTranscriptBridge`
/// (app/voice_transcript_bridge.py) expects.
@MainActor
final class VoiceSessionViewModel: ObservableObject {
    enum SessionState: Equatable {
        case idle
        case connecting
        case listening
        case stopped
    }

    @Published private(set) var state: SessionState = .idle
    @Published var lastTranscript: String = ""
    @Published var errorMessage: String?

    private let baseURL = "http://127.0.0.1:8000"
    private var room: Room?
    private let transcriptionService = VoiceTranscriptionService()
    private var eventTask: Task<Void, Never>?

    private static let responseDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    func start(accessToken: String?) async {
        guard state == .idle || state == .stopped else { return }
        errorMessage = nil
        state = .connecting

        guard let accessToken else {
            errorMessage = "Your session expired. Please sign in again."
            state = .idle
            return
        }

        do {
            let voiceToken = try await fetchVoiceToken(accessToken: accessToken)

            let room = Room()
            self.room = room
            try await room.connect(url: voiceToken.url, token: voiceToken.token)

            try await transcriptionService.start()

            eventTask = Task { [weak self] in
                guard let self else { return }
                for await event in self.transcriptionService.events {
                    await self.handle(event)
                }
            }

            state = .listening
        } catch let error as VoiceTranscriptionError {
            errorMessage = error.errorDescription
            await tearDownAfterFailure()
        } catch {
            errorMessage = "Could not start a voice session: \(error.localizedDescription)"
            await tearDownAfterFailure()
        }
    }

    /// Stops transcription first and waits for `eventTask` to drain (so a
    /// trailing `.speechStopped` from an open turn still gets published)
    /// before disconnecting from the room.
    func stop() async {
        guard state == .listening || state == .connecting else { return }

        await transcriptionService.stop()
        await eventTask?.value
        eventTask = nil

        await room?.disconnect()
        room = nil
        state = .stopped
    }

    private func tearDownAfterFailure() async {
        eventTask?.cancel()
        eventTask = nil
        await transcriptionService.stop()
        await room?.disconnect()
        room = nil
        state = .idle
    }

    private func handle(_ event: VoiceTranscriptEvent) async {
        switch event {
        case .speechStarted:
            await publish(VoiceDataMessage(type: "speech_started", text: nil))
        case .interim(let text):
            lastTranscript = text
            await publish(VoiceDataMessage(type: "interim", text: text))
        case .final(let text):
            lastTranscript = text
            await publish(VoiceDataMessage(type: "final", text: text))
        case .speechStopped:
            await publish(VoiceDataMessage(type: "speech_stopped", text: nil))
        }
    }

    private func publish(_ message: VoiceDataMessage) async {
        guard let room else { return }
        do {
            let data = try JSONEncoder().encode(message)
            // Sanity check, not a permanent log line: the backend bridge
            // matches on exact "type"/"text" keys (see
            // app/voice_transcript_bridge.py) -- this confirms the encoder
            // (no keyEncodingStrategy set here, so property names are used
            // as-is) actually produces that shape rather than something
            // JSONEncoder's snake_case conversion could have silently broken.
            #if DEBUG
            if let json = String(data: data, encoding: .utf8) {
                print("VoiceSessionViewModel: publishing data message \(json)")
            }
            #endif
            try await room.localParticipant.publish(data: data, options: DataPublishOptions(reliable: true))
        } catch {
            errorMessage = "Failed to send transcript to Atlas: \(error.localizedDescription)"
        }
    }

    private func fetchVoiceToken(accessToken: String) async throws -> VoiceTokenResponse {
        var urlRequest = URLRequest(url: URL(string: "\(baseURL)/voice/token")!)
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: urlRequest)
        guard let http = response as? HTTPURLResponse else {
            throw VoiceSessionError.tokenFetchFailed("No response from server.")
        }
        guard http.statusCode == 200 else {
            throw VoiceSessionError.tokenFetchFailed(
                http.statusCode == 401
                    ? "Your session expired. Please sign in again."
                    : "Failed to start a voice session (server returned \(http.statusCode))."
            )
        }
        return try Self.responseDecoder.decode(VoiceTokenResponse.self, from: data)
    }
}

private struct VoiceTokenResponse: Decodable {
    let url: String
    let roomName: String
    let token: String
}

/// Property names double as the exact JSON keys the backend expects
/// (`type`/`text`) -- no `keyEncodingStrategy` is set on the encoder used to
/// serialize this, so nothing rewrites them. `text` is `Optional` so that
/// `speech_started`/`speech_stopped` messages omit the key entirely
/// (Swift's synthesized `Encodable` conformance calls `encodeIfPresent` for
/// `Optional` properties), matching the backend's documented message shape
/// exactly rather than sending `"text": null`.
private struct VoiceDataMessage: Encodable {
    let type: String
    let text: String?
}

private enum VoiceSessionError: LocalizedError {
    case tokenFetchFailed(String)

    var errorDescription: String? {
        switch self {
        case .tokenFetchFailed(let message):
            return message
        }
    }
}
