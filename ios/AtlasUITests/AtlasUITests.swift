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
        extract(app, text: "Let's meet next Thursday at 3pm to sync on the launch.")

        let firstEventTitle = app.textFields.matching(identifier: "EventTitle").firstMatch
        XCTAssertTrue(
            firstEventTitle.waitForExistence(timeout: 30),
            "Expected at least one extracted event to appear -- is the backend running on 127.0.0.1:8000?"
        )

        XCTAssertFalse(app.staticTexts["ExtractErrorMessage"].exists)
    }

    func testNoEventsFoundStateForNonSchedulingText() throws {
        let app = XCUIApplication()
        app.launch()
        extract(app, text: "Thanks for the update, sounds great.")

        let noEventsLabel = app.staticTexts["NoEventsFoundLabel"]
        XCTAssertTrue(noEventsLabel.waitForExistence(timeout: 30))
        XCTAssertFalse(app.otherElements["EventList"].exists)
    }

    func testEditingTitleUpdatesTheField() throws {
        let app = XCUIApplication()
        app.launch()
        extract(app, text: "Let's meet next Thursday at 3pm to sync on the launch.")

        let title = app.textFields.matching(identifier: "EventTitle").firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 30))

        title.tap()
        // Select-all then type, since the field already has extracted text in it.
        title.press(forDuration: 1.0)
        if app.menuItems["Select All"].waitForExistence(timeout: 2) {
            app.menuItems["Select All"].tap()
        }
        title.typeText("Corrected Title")

        XCTAssertTrue((title.value as? String)?.contains("Corrected Title") ?? false)
    }

    func testUnresolvableDateShowsWarningOnTheDateField() throws {
        let app = XCUIApplication()
        app.launch()
        extract(app, text: "We should grab coffee sometime soon, let me know what works.")

        let warning = app.staticTexts["EventDateWarning"]
        XCTAssertTrue(warning.waitForExistence(timeout: 30))
    }

    func testAddEndTimeTogglesEndDatePicker() throws {
        let app = XCUIApplication()
        app.launch()
        extract(app, text: "Let's meet next Thursday at 3pm to sync on the launch.")

        XCTAssertTrue(app.textFields.matching(identifier: "EventTitle").firstMatch.waitForExistence(timeout: 30))

        let addEndToggle = app.switches["EventHasEndToggle"]
        XCTAssertTrue(addEndToggle.exists)
        XCTAssertFalse(app.datePickers["EventEndDatePicker"].exists)

        // The row-wide "EventHasEndToggle" element's center point lands on
        // the label text, not the actual switch control at the row's right
        // edge -- tap the nested control switch specifically.
        addEndToggle.switches.firstMatch.tap()

        XCTAssertTrue(app.datePickers["EventEndDatePicker"].waitForExistence(timeout: 5))
    }

    private func extract(_ app: XCUIApplication, text: String) {
        let textEditor = app.textViews["EmailTextEditor"]
        XCTAssertTrue(textEditor.waitForExistence(timeout: 5))
        textEditor.tap()
        textEditor.typeText(text)
        app.buttons["ExtractButton"].tap()
    }
}
