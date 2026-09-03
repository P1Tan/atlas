import XCTest

/// Milestone 7.2b wiring check, not a transcription-accuracy test --
/// assistant-spec.md §18 is explicit that the Simulator can't validate real
/// mic/latency behavior, so this deliberately never asserts on transcribed
/// text content. It only confirms the permission -> token -> room-connect ->
/// transcription -> stop path doesn't crash and reaches a sane state.
@MainActor
final class VoiceDebugUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testStartThenStopDoesNotCrashAndReachesAVisibleState() async throws {
        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)

        // The Simulator prompts for mic/speech-recognition access on first
        // use of AVAudioEngine/SFSpeechRecognizer -- these are system alerts
        // outside the app's own view hierarchy, so XCUITest needs an
        // interruption monitor (a normal element query never sees them).
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

        let tabBar = app.tabBars.firstMatch
        XCTAssertTrue(tabBar.waitForExistence(timeout: 10))

        let voiceTab = app.tabBars.buttons["Voice (Debug)"]
        XCTAssertTrue(voiceTab.waitForExistence(timeout: 5), "Expected a DEBUG-only Voice tab in a Debug build.")
        voiceTab.tap()

        let startButton = app.buttons["VoiceDebugStartButton"]
        XCTAssertTrue(startButton.waitForExistence(timeout: 5))
        XCTAssertTrue(startButton.isEnabled)
        startButton.tap()

        // Nudges XCUITest to process any pending system permission alert
        // registered above -- interruption monitors only fire on the next
        // interaction with the app.
        app.tap()

        let stopButton = app.buttons["VoiceDebugStopButton"]
        let errorMessage = app.staticTexts["VoiceDebugErrorMessage"]

        // Either a real listening state (stop button enabled) or a clearly
        // surfaced error (permission denial, token fetch failure, room
        // connect failure) is an acceptable outcome here -- what matters is
        // that the app didn't crash and gave *some* legible result.
        let reachedAKnownState = waitUntil(timeout: 20) {
            stopButton.isEnabled || errorMessage.exists
        }
        XCTAssertTrue(
            reachedAKnownState,
            "Expected either a listening state (enabled stop button) or a surfaced error message after starting a voice session."
        )

        if stopButton.isEnabled {
            stopButton.tap()
            XCTAssertTrue(waitUntil(timeout: 10) { !stopButton.isEnabled })
        }

        XCTAssertEqual(app.state, .runningForeground, "The app should still be running after a start/stop cycle.")
    }

    /// `waitForExistence`-style polling for an arbitrary condition, since
    /// the outcome here (listening vs. error) isn't a single element's
    /// existence.
    private func waitUntil(timeout: TimeInterval, condition: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        return condition()
    }
}
