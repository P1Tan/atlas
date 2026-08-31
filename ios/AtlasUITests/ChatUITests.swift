import XCTest

/// Requires the backend running locally at 127.0.0.1:8000 (see backend/README)
/// -- drives the real /chat endpoint and a real GPT-5 mini call, not a mock.
/// Prompts ask for exact, deterministic wording so assertions aren't flaky.
@MainActor
final class ChatUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testSendButtonDisabledUntilTextEntered() async throws {
        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)

        let input = app.textFields["ChatInputField"]
        XCTAssertTrue(input.waitForExistence(timeout: 5))

        let sendButton = app.buttons["ChatSendButton"]
        XCTAssertTrue(sendButton.exists)
        XCTAssertFalse(sendButton.isEnabled)

        input.tap()
        input.typeText("Hello")

        XCTAssertTrue(sendButton.isEnabled)
    }

    func testSendingAMessageProducesAnAssistantReply() async throws {
        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)

        let input = app.textFields["ChatInputField"]
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("Reply with exactly the single word: PONG")
        app.buttons["ChatSendButton"].tap()

        let userBubble = app.staticTexts.matching(identifier: "ChatUserMessage").firstMatch
        XCTAssertTrue(userBubble.waitForExistence(timeout: 5))

        let assistantBubble = app.staticTexts.matching(identifier: "ChatAssistantMessage").firstMatch
        XCTAssertTrue(
            assistantBubble.waitForExistence(timeout: 30),
            "Expected an assistant reply -- is the backend running on 127.0.0.1:8000?"
        )
        XCTAssertTrue(assistantBubble.label.contains("PONG"))
        XCTAssertFalse(app.staticTexts["ChatErrorMessage"].exists)
    }
}
