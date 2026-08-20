import XCTest

/// Requires the backend running locally at 127.0.0.1:8000 (see backend/README)
/// for the extraction flow tests -- these drive the real network call, not a
/// mock, since the point is confirming the app and backend agree end to end.
final class AtlasUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testExtractButtonDisabledUntilTextEntered() throws {
        let app = XCUIApplication()
        app.launch()

        let textEditor = app.textViews["EmailTextEditor"]
        XCTAssertTrue(textEditor.waitForExistence(timeout: 5))

        let extractButton = app.buttons["ExtractButton"]
        XCTAssertTrue(extractButton.exists)
        XCTAssertFalse(extractButton.isEnabled)

        textEditor.tap()
        textEditor.typeText("Lunch next Tuesday at noon.")

        XCTAssertTrue(extractButton.isEnabled)
    }

    func testPasteInputExtractsAndDisplaysEvents() throws {
        let app = XCUIApplication()
        app.launch()

        let textEditor = app.textViews["EmailTextEditor"]
        XCTAssertTrue(textEditor.waitForExistence(timeout: 5))
        textEditor.tap()
        textEditor.typeText("Let's meet next Thursday at 3pm to sync on the launch.")

        let extractButton = app.buttons["ExtractButton"]
        XCTAssertTrue(extractButton.isEnabled)
        extractButton.tap()

        let firstEventTitle = app.staticTexts.matching(identifier: "EventTitle").firstMatch
        XCTAssertTrue(
            firstEventTitle.waitForExistence(timeout: 30),
            "Expected at least one extracted event to appear -- is the backend running on 127.0.0.1:8000?"
        )

        let errorMessage = app.staticTexts["ExtractErrorMessage"]
        XCTAssertFalse(errorMessage.exists)
    }

    func testNoEventsFoundStateForNonSchedulingText() throws {
        let app = XCUIApplication()
        app.launch()

        let textEditor = app.textViews["EmailTextEditor"]
        XCTAssertTrue(textEditor.waitForExistence(timeout: 5))
        textEditor.tap()
        textEditor.typeText("Thanks for the update, sounds great.")

        app.buttons["ExtractButton"].tap()

        let noEventsLabel = app.staticTexts["NoEventsFoundLabel"]
        XCTAssertTrue(noEventsLabel.waitForExistence(timeout: 30))
        XCTAssertFalse(app.otherElements["EventList"].exists)
    }
}
