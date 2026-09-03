import AVFoundation
import Foundation
import Speech

/// Turn-boundary and transcript events produced by `VoiceTranscriptionService`,
/// in the same order the backend's `LiveKitTranscriptBridge` expects them
/// per utterance: `speechStarted`, zero or more `interim`, exactly one
/// `final`, `speechStopped`.
enum VoiceTranscriptEvent {
    case speechStarted
    case interim(String)
    case final(String)
    case speechStopped
}

enum VoiceTranscriptionError: LocalizedError {
    case microphonePermissionDenied
    case speechRecognitionPermissionDenied
    case transcriberUnavailable
    case audioEngineFailed(String)

    var errorDescription: String? {
        switch self {
        case .microphonePermissionDenied:
            return "Microphone access is required to talk to Atlas. Enable it in Settings > Atlas."
        case .speechRecognitionPermissionDenied:
            return "Speech recognition access is required to talk to Atlas. Enable it in Settings > Atlas."
        case .transcriberUnavailable:
            return "On-device speech transcription isn't available on this device right now."
        case .audioEngineFailed(let message):
            return "Could not start audio capture: \(message)"
        }
    }
}

/// On-device speech-to-text for Milestone 7.2b, built on iOS 26's
/// `SpeechAnalyzer`/`SpeechTranscriber` (transcript content) plus a simple
/// RMS-threshold silence detector on the raw mic buffer (turn boundaries).
///
/// Deliberately does NOT use `SpeechTranscriber.Result.isFinal` as a
/// turn/pause boundary signal. Per Apple's WWDC25 SpeechAnalyzer session,
/// `isFinal` marks Apple's own internal rolling phrase-chunking ("finalizes
/// a phrase, moves on to the next range") -- it is unrelated to the user
/// pausing or stopping talking. `isFinal` is used here only to decide
/// *which* text becomes a `.final` event's content; turn boundaries
/// (`.speechStarted` / `.speechStopped`) come entirely from the RMS silence
/// state machine below, a standard community technique for this exact
/// problem (see `smart-turn-ios`, a Swift port of Pipecat's own
/// turn-detection model).
///
/// The RMS threshold and ~1.5s silence window are a first-pass heuristic,
/// not precisely calibrated -- there was no way to validate them against
/// real mic input/room noise in this environment (the Simulator can't
/// validate real mic behavior; see assistant-spec.md §18). Expect to need
/// real-device tuning.
///
/// Not `@MainActor` and not an `actor`: the `AVAudioEngine` input tap fires
/// on a dedicated real-time audio thread, and hopping through actor
/// isolation on every buffer would add latency for no benefit here. Mutable
/// state touched from that thread is guarded by `stateLock` instead.
final class VoiceTranscriptionService: @unchecked Sendable {
    let events: AsyncStream<VoiceTranscriptEvent>
    private let eventContinuation: AsyncStream<VoiceTranscriptEvent>.Continuation

    private let audioEngine = AVAudioEngine()
    private var transcriber: SpeechTranscriber?
    private var analyzer: SpeechAnalyzer?
    private var analyzerInputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var analyzeTask: Task<Void, Never>?
    private var resultsTask: Task<Void, Never>?
    private var tapInstalled = false

    // First-pass heuristic thresholds -- see the type doc comment above.
    private let silenceThreshold: Float = 0.02
    private let silenceDuration: TimeInterval = 1.5

    private let stateLock = NSLock()
    private var isSpeaking = false
    private var lastAboveThresholdTime = Date.distantPast

    init() {
        let (stream, continuation) = AsyncStream.makeStream(of: VoiceTranscriptEvent.self)
        events = stream
        eventContinuation = continuation
    }

    /// Requests mic + speech authorization, sets up the analyzer/transcriber
    /// and audio engine, and begins emitting events on `events`. Throws
    /// (rather than failing silently) on permission denial or setup failure.
    func start() async throws {
        guard await requestMicrophonePermission() else {
            throw VoiceTranscriptionError.microphonePermissionDenied
        }
        // Requested defensively even though the new SpeechAnalyzer/
        // SpeechTranscriber API's exact reliance on this authorization is
        // ambiguous in Apple's current docs -- every real-world reference
        // implementation still requests it, and NSSpeechRecognitionUsageDescription
        // is in Info.plist regardless (see project.yml).
        guard await requestSpeechPermission() else {
            throw VoiceTranscriptionError.speechRecognitionPermissionDenied
        }

        let transcriber = SpeechTranscriber(
            locale: Locale.current,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: []
        )
        self.transcriber = transcriber

        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }

        guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber])
        else {
            throw VoiceTranscriptionError.transcriberUnavailable
        }

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        self.analyzer = analyzer

        let (analyzerInputSequence, analyzerInputContinuation) = AsyncStream.makeStream(of: AnalyzerInput.self)
        self.analyzerInputContinuation = analyzerInputContinuation

        resultsTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await result in transcriber.results {
                    let text = String(result.text.characters)
                    if result.isFinal {
                        self.emit(.final(text))
                    } else {
                        self.emit(.interim(text))
                    }
                }
            } catch {
                // The results stream ended (analyzer finalized/cancelled) or
                // errored -- either way, nothing further to relay.
            }
        }

        analyzeTask = Task {
            _ = try? await analyzer.analyzeSequence(analyzerInputSequence)
        }

        try startAudioEngine(targetFormat: analyzerFormat)
    }

    /// Stops audio capture and finalizes the analyzer, draining any
    /// remaining transcript results and emitting a final `.speechStopped`
    /// if a turn was still open, before finishing `events`.
    func stop() async {
        if tapInstalled {
            audioEngine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        if audioEngine.isRunning {
            audioEngine.stop()
        }

        analyzerInputContinuation?.finish()
        analyzerInputContinuation = nil

        try? await analyzer?.finalizeAndFinishThroughEndOfInput()

        await analyzeTask?.value
        analyzeTask = nil

        resultsTask?.cancel()
        await resultsTask?.value
        resultsTask = nil

        // Same atomicity requirement as updateSilenceState above -- decide
        // and emit under one lock acquisition, not two separate ones.
        stateLock.lock()
        let wasSpeaking = isSpeaking
        isSpeaking = false
        if wasSpeaking {
            emit(.speechStopped)
        }
        stateLock.unlock()

        eventContinuation.finish()
    }

    // MARK: - Audio engine

    private func startAudioEngine(targetFormat: AVAudioFormat) throws {
        let inputNode = audioEngine.inputNode
        let nativeFormat = inputNode.outputFormat(forBus: 0)

        guard let converter = AVAudioConverter(from: nativeFormat, to: targetFormat) else {
            throw VoiceTranscriptionError.audioEngineFailed("could not create an audio converter for the input format")
        }

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) { [weak self] buffer, _ in
            self?.handleTap(buffer: buffer, converter: converter, targetFormat: targetFormat)
        }
        tapInstalled = true

        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            inputNode.removeTap(onBus: 0)
            tapInstalled = false
            throw VoiceTranscriptionError.audioEngineFailed(error.localizedDescription)
        }
    }

    /// Runs on the audio engine's real-time render thread -- keep this
    /// cheap: RMS over the buffer, a converted copy handed to the analyzer,
    /// no allocSelf beyond what AVAudioConverter itself needs.
    private func handleTap(buffer: AVAudioPCMBuffer, converter: AVAudioConverter, targetFormat: AVAudioFormat) {
        updateSilenceState(with: buffer)

        guard let converted = convert(buffer: buffer, using: converter, to: targetFormat) else { return }
        analyzerInputContinuation?.yield(AnalyzerInput(buffer: converted))
    }

    private func convert(
        buffer: AVAudioPCMBuffer, using converter: AVAudioConverter, to format: AVAudioFormat
    ) -> AVAudioPCMBuffer? {
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard let outputBuffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }

        var conversionError: NSError?
        var suppliedInput = false
        let inputBlock: AVAudioConverterInputBlock = { _, outStatus in
            if suppliedInput {
                outStatus.pointee = .noDataNow
                return nil
            }
            suppliedInput = true
            outStatus.pointee = .haveData
            return buffer
        }

        converter.convert(to: outputBuffer, error: &conversionError, withInputFrom: inputBlock)
        if conversionError != nil { return nil }
        return outputBuffer
    }

    // MARK: - RMS silence state machine

    /// Declares `.speechStarted` on a silent -> speaking transition (RMS
    /// crosses above `silenceThreshold`), and `.speechStopped` once RMS has
    /// stayed below it for `silenceDuration`. Driven purely by the tap's own
    /// cadence -- the input node keeps delivering buffers (silent ones
    /// included) for as long as the engine runs, so no separate timer is
    /// needed to notice "still silent."
    private func updateSilenceState(with buffer: AVAudioPCMBuffer) {
        guard let channelData = buffer.floatChannelData else { return }
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return }

        let samples = channelData[0]
        var sumOfSquares: Float = 0
        for i in 0..<frameCount {
            let sample = samples[i]
            sumOfSquares += sample * sample
        }
        let rms = sqrt(sumOfSquares / Float(frameCount))
        let isAboveThreshold = rms > silenceThreshold
        let now = Date()

        // The state transition and its emit() must happen atomically under
        // one lock acquisition -- stop()'s forced-stop path takes the same
        // lock for its own decide-then-emit, and unlocking between "decide"
        // and "emit" here left a window where stop() could interleave, see
        // stale state, and emit .speechStopped before this thread's
        // .speechStarted for the same turn ever went out. emit() only
        // touches eventContinuation, never stateLock, so holding the lock
        // across it cannot deadlock.
        stateLock.lock()
        if isAboveThreshold {
            lastAboveThresholdTime = now
        }
        let currentlySpeaking = isSpeaking
        let silentFor = now.timeIntervalSince(lastAboveThresholdTime)

        if isAboveThreshold && !currentlySpeaking {
            isSpeaking = true
            emit(.speechStarted)
        } else if !isAboveThreshold && currentlySpeaking && silentFor >= silenceDuration {
            isSpeaking = false
            emit(.speechStopped)
        }
        stateLock.unlock()
    }

    // MARK: - Permissions

    private func requestMicrophonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    private func requestSpeechPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
    }

    private func emit(_ event: VoiceTranscriptEvent) {
        eventContinuation.yield(event)
    }
}
