import XCTest

/// Only covers local UI state (no network) -- deliberately does NOT exercise
/// a real signInWithOTP call, since Supabase's free-tier email-sending rate
/// limit is tight enough that a real send here on every test run would
/// exhaust it (hit during manual verification of this same milestone).
@MainActor
final class SignInUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testSendButtonDisabledUntilEmailEntered() throws {
        let app = XCUIApplication()
        // A real session may already be persisted from an earlier run (see
        // AuthViewModel.bootstrap()'s doc comment) -- force the signed-out
        // state so this test exercises SignInView deterministically.
        app.launchEnvironment["ATLAS_TEST_FORCE_SIGNED_OUT"] = "1"
        app.launch()

        let emailField = app.textFields["SignInEmailField"]
        XCTAssertTrue(emailField.waitForExistence(timeout: 5))

        let sendButton = app.buttons["SendMagicLinkButton"]
        XCTAssertTrue(sendButton.exists)
        XCTAssertFalse(sendButton.isEnabled)

        emailField.tap()
        emailField.typeText("someone@example.com")

        XCTAssertTrue(sendButton.isEnabled)
    }
}
