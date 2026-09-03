import XCTest

/// Milestone 7.4b integrated voice UI wiring check, superseding the retired
/// `VoiceDebugUITests` (see `git show HEAD~1:ios/AtlasUITests/VoiceDebugUITests.swift`
/// for the interruption-monitor pattern this carries forward). Same honesty
/// bar as that file: assistant-spec.md §18 is explicit that the Simulator
/// can't validate real mic input or transcription accuracy, so these tests
/// never assert on transcribed text content -- they only confirm the
/// integrated Chat screen's voice controls (mic / cancel / stop / replay)
/// reach the expected UI states and never crash the app.
@MainActor
final class VoiceChatUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// Tap-to-start, then the separate "X" cancel action: should return to a
    /// plain idle state (mic button available again, listening indicator
    /// gone) without sending anything to the transcript.
    func testMicTapThenCancelReturnsToIdle() async throws {
        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)
        installVoicePermissionInterruptionMonitor()

        let micButton = app.buttons["VoiceMicButton"]
        XCTAssertTrue(micButton.waitForExistence(timeout: 5))
        micButton.tap()

        // Nudges XCUITest to process any pending system permission alert.
        app.tap()

        let listeningIndicator = app.descendants(matching: .any)["VoiceListeningIndicator"]
        let cancelButton = app.buttons["VoiceCancelButton"]
        let errorMessage = app.staticTexts["ChatErrorMessage"]

        let reachedAKnownState = waitUntil(timeout: 20) {
            cancelButton.exists || errorMessage.exists
        }
        XCTAssertTrue(
            reachedAKnownState,
            "Expected either a listening state (visible cancel button) or a surfaced error after tapping the mic."
        )

        if cancelButton.exists {
            XCTAssertTrue(listeningIndicator.exists || listeningIndicator.waitForExistence(timeout: 2))
            cancelButton.tap()

            XCTAssertTrue(waitUntil(timeout: 10) { !cancelButton.exists })
            XCTAssertTrue(micButton.waitForExistence(timeout: 5))
            XCTAssertTrue(micButton.isEnabled, "Expected the mic button to be available again after cancel.")
        }

        XCTAssertEqual(app.state, .runningForeground, "The app should still be running after mic-tap-then-cancel.")
    }

    /// Tap-to-start, then tap-to-stop (the mic button itself, normal
    /// completion rather than cancel): should not crash and should leave the
    /// app in a legible state (back to idle, or a thinking/speaking state if
    /// a real backend round trip is in flight, or a surfaced error).
    ///
    /// Milestone 7.4b-fixes (Finding 0): `VoiceSessionController.stopVoiceTurn()`
    /// used to disconnect the LiveKit room synchronously, before the
    /// assistant's reply (a full LLM+TTS round trip) could ever arrive --
    /// which meant `.thinking`/`.speaking` were structurally unreachable via
    /// this tap-to-stop path. That's now fixed: the room stays connected
    /// after tap-to-stop until the reply genuinely finishes or the user
    /// cancels. This test still can't *prove* that end-to-end from the
    /// Simulator, though -- per this file's own stated honesty constraint
    /// (assistant-spec.md §18), the Simulator has no real mic input, so
    /// on-device transcription essentially never produces a `.final`
    /// transcript here, and `ChatViewModel.stopVoiceTurn()`'s own documented
    /// fallback ("if nothing was ever captured... fall back to idle") means
    /// this test will almost always observe an immediate return to `.idle`,
    /// never `.thinking`/`.speaking`, regardless of whether Finding 0's fix
    /// is present. What IS meaningfully checkable from here, and what this
    /// rewrite actually asserts on:
    ///  1. Tapping stop reaches a definite, legible state (not silently
    ///     ignored) -- the discarded-result bug this replaces (Finding 5).
    ///  2. If a thinking/speaking state IS somehow reached (e.g. background
    ///     Simulator audio happens to trip the RMS speech detector), the
    ///     state machine does not get stuck there forever -- it eventually
    ///     returns to an idle, mic-available state, which is exactly the
    ///     "doesn't get stuck" invariant Finding 0 requires of the redesign.
    func testMicTapThenStopDoesNotCrashAndReachesASaneState() async throws {
        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)
        installVoicePermissionInterruptionMonitor()

        let micButton = app.buttons["VoiceMicButton"]
        XCTAssertTrue(micButton.waitForExistence(timeout: 5))
        micButton.tap()
        app.tap()

        let cancelButton = app.buttons["VoiceCancelButton"]
        let errorMessage = app.staticTexts["ChatErrorMessage"]

        let reachedListeningOrError = waitUntil(timeout: 20) {
            cancelButton.exists || errorMessage.exists
        }
        XCTAssertTrue(
            reachedListeningOrError,
            "Expected either a listening state (visible cancel button) or a surfaced error after tapping the mic."
        )

        if cancelButton.exists {
            // Tap the mic button again -- normal stop, not cancel.
            micButton.tap()

            let thinkingIndicator = app.descendants(matching: .any)["VoiceThinkingIndicator"]
            let speakingIndicator = app.descendants(matching: .any)["VoiceSpeakingIndicator"]
            let reachedStoppedThinkingOrSpeaking = waitUntil(timeout: 15) {
                !cancelButton.exists || thinkingIndicator.exists || speakingIndicator.exists
            }
            XCTAssertTrue(
                reachedStoppedThinkingOrSpeaking,
                "Expected tap-to-stop to leave the listening state -- either straight back to idle "
                    + "(the expected outcome given the Simulator's lack of real mic input) or into "
                    + "thinking/speaking (if a real turn was somehow captured), not stuck listening forever."
            )

            // Finding 0's core invariant: whatever state was just reached,
            // the state machine must not get stuck there. If thinking/
            // speaking was reached, it must eventually resolve back to an
            // idle, mic-available state (via a real backend reply, or --
            // worst case -- VoiceSessionController's own 30s
            // awaitingReplyTimeout safety net) rather than hanging forever.
            // If idle was already reached above, this is an immediate no-op
            // pass.
            let returnedToIdleMicAvailable = waitUntil(timeout: 40) {
                micButton.exists && micButton.isEnabled
            }
            XCTAssertTrue(
                returnedToIdleMicAvailable,
                "Expected the voice state machine to eventually return to an idle, mic-available state "
                    + "rather than getting stuck in thinking/speaking."
            )
        }

        XCTAssertEqual(app.state, .runningForeground, "The app should still be running after mic-tap-then-stop.")
        XCTAssertTrue(micButton.exists, "Expected the Chat screen's mic button to still be present.")
    }

    /// The Simulator prompts for mic/speech-recognition access on first use
    /// of `AVAudioEngine`/`SFSpeechRecognizer` -- these are system alerts
    /// outside the app's own view hierarchy, so XCUITest needs an
    /// interruption monitor (a normal element query never sees them).
    private func installVoicePermissionInterruptionMonitor() {
        addUIInterruptionMonitor(withDescription: "Microphone/Speech Recognition Permission") { alert in
            for title in ["Allow", "OK", "Allow While Using App"] {
                let button = alert.buttons[title]
                if button.exists {
                    button.tap()
                    return true
                }
            }
            return false
        }
    }

    /// `waitForExistence`-style polling for an arbitrary condition, since the
    /// outcomes checked here aren't always a single element's existence.
    private func waitUntil(timeout: TimeInterval, condition: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        return condition()
    }
}
