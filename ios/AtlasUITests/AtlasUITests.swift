import XCTest

/// Requires the backend running locally at 127.0.0.1:8000 (see backend/README)
/// for the extraction flow tests -- these drive the real network call, not a
/// mock, since the point is confirming the app and backend agree end to end.
///
/// There is no paste box any more, so text gets into extraction the way a
/// real user's does: through `ShareInbox`. `launchSharing(_:text:)` seeds it
/// via the app's DEBUG-only ATLAS_TEST_SHARE_TEXT launch environment (see
/// `AtlasApp`), which stands in for the share sheet -- XCUITest can't drive
/// another app's share sheet into this one. From there it is entirely the
/// production path: ContentView switches to the Email tab and PasteInputView
/// auto-extracts, so these tests only wait for the results.
@MainActor
final class AtlasUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testSharedTextExtractsAndDisplaysEvents() async throws {
        let app = XCUIApplication()
        try await launchSharing(app, text: "Let's meet next Thursday at 3pm to sync on the launch.")

        let firstEventTitle = app.textFields.matching(identifier: "EventTitle").firstMatch
        XCTAssertTrue(
            firstEventTitle.waitForExistence(timeout: 30),
            "Expected at least one extracted event to appear -- is the backend running on 127.0.0.1:8000?"
        )

        XCTAssertFalse(app.staticTexts["ExtractErrorMessage"].exists)
    }

    func testNoEventsFoundStateForNonSchedulingText() async throws {
        let app = XCUIApplication()
        try await launchSharing(app, text: "Thanks for the update, sounds great.")

        let noEventsLabel = app.staticTexts["NoEventsFoundLabel"]
        XCTAssertTrue(noEventsLabel.waitForExistence(timeout: 30))
        XCTAssertFalse(app.otherElements["EventList"].exists)
    }

    func testEditingTitleUpdatesTheField() async throws {
        let app = XCUIApplication()
        try await launchSharing(app, text: "Let's meet next Thursday at 3pm to sync on the launch.")

        let title = app.textFields.matching(identifier: "EventTitle").firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 30))

        title.tap()
        sleep(1) // let the keyboard-appearance layout shift settle before a long-press
        // Select-all then type, since the field already has extracted text in it.
        title.press(forDuration: 1.0)
        if app.menuItems["Select All"].waitForExistence(timeout: 2) {
            app.menuItems["Select All"].tap()
        }
        title.typeText("Corrected Title")

        XCTAssertTrue((title.value as? String)?.contains("Corrected Title") ?? false)
    }

    func testUnresolvableDateShowsWarningOnTheDateField() async throws {
        let app = XCUIApplication()
        try await launchSharing(app, text: "We should grab coffee sometime soon, let me know what works.")

        let warning = app.staticTexts["EventDateWarning"]
        XCTAssertTrue(warning.waitForExistence(timeout: 30))
    }

    func testAddEndTimeTogglesEndDatePicker() async throws {
        let app = XCUIApplication()
        try await launchSharing(app, text: "Let's meet next Thursday at 3pm to sync on the launch.")

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

    /// Requires calendar access pre-granted for com.p1tan.atlas, e.g.
    /// `xcrun simctl privacy <device> grant calendar com.p1tan.atlas`,
    /// so this exercises the actual EventKit write, not a permission prompt.
    ///
    /// The title is randomised per run because this writes to the real
    /// simulator calendar and never cleans up: with the fixed "next Thursday
    /// 3pm" title this test used to add, every past run left a copy behind,
    /// and `CalendarWriter`'s duplicate guard would now (correctly) answer
    /// "Already on Calendar" on the very first add. A unique title makes the
    /// first add genuinely new -- and lets the second half of this test
    /// assert the guard against an event it just created itself, rather than
    /// against whatever residue an earlier run happened to leave.
    func testAddToCalendarWritesEventThenRefusesTheDuplicate() async throws {
        let app = XCUIApplication()
        let shareText = "Let's meet next Thursday at 3pm to sync on the launch."
        let uniqueTitle = "Atlas UITest \(UUID().uuidString.prefix(8))"

        try await launchSharing(app, text: shareText)
        setEventTitle(app, to: uniqueTitle)

        let addButton = app.buttons["AddToCalendarButton"]
        XCTAssertTrue(addButton.exists)
        addButton.tap()

        let addedPredicate = NSPredicate(format: "label CONTAINS 'Added to Calendar'")
        let addedExpectation = XCTNSPredicateExpectation(predicate: addedPredicate, object: addButton)
        XCTAssertEqual(XCTWaiter().wait(for: [addedExpectation], timeout: 10), .completed)
        XCTAssertFalse(addButton.isEnabled)
        XCTAssertFalse(app.staticTexts["AddToCalendarError"].exists)

        // Same text, same title, fresh launch: nothing in a new DraftEvent
        // remembers the earlier write, so only the calendar itself can catch
        // this -- which is exactly the case the guard exists for.
        try await launchSharing(app, text: shareText)
        setEventTitle(app, to: uniqueTitle)

        let addButtonAgain = app.buttons["AddToCalendarButton"]
        XCTAssertTrue(addButtonAgain.exists)
        addButtonAgain.tap()

        let duplicatePredicate = NSPredicate(format: "label CONTAINS 'Already on Calendar'")
        let duplicateExpectation = XCTNSPredicateExpectation(predicate: duplicatePredicate, object: addButtonAgain)
        XCTAssertEqual(XCTWaiter().wait(for: [duplicateExpectation], timeout: 10), .completed)
        XCTAssertFalse(addButtonAgain.isEnabled)
        XCTAssertFalse(app.staticTexts["AddToCalendarError"].exists)
    }

    /// Replaces the first event row's extracted title with `newTitle`, using
    /// the same select-all-then-type dance as
    /// `testEditingTitleUpdatesTheField`, and dismisses the keyboard
    /// afterwards -- otherwise it sits on top of the Add button and the tap
    /// lands on a key instead.
    private func setEventTitle(_ app: XCUIApplication, to newTitle: String) {
        let title = app.textFields.matching(identifier: "EventTitle").firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 30))

        title.tap()
        sleep(1) // let the keyboard-appearance layout shift settle before a long-press
        title.press(forDuration: 1.0)
        if app.menuItems["Select All"].waitForExistence(timeout: 2) {
            app.menuItems["Select All"].tap()
        }
        title.typeText(newTitle + "\n")
        sleep(1) // and again on the way back down
    }

    /// Assumes Gmail is already connected on this machine (real OAuth
    /// completed in Increment 3.1) -- doesn't tap the button, since that
    /// would fetch real unread mail. Doesn't cover the disconnected state,
    /// since forcing a disconnect here would blow away a real, working
    /// credential just for a UI assertion.
    func testCheckGmailButtonAppearsWhenGmailIsConnected() async throws {
        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)
        app.tabBars.buttons["Email"].tap()

        XCTAssertTrue(app.buttons["CheckGmailButton"].waitForExistence(timeout: 10))
    }

    /// Launches signed in with `text` already waiting in `ShareInbox`, exactly
    /// as a share-sheet hand-off leaves it. Set before `launchSignedIn`
    /// because that is what calls `app.launch()`; `launchEnvironment` is
    /// additive, so the auth variables it sets are unaffected.
    private func launchSharing(_ app: XCUIApplication, text: String) async throws {
        app.launchEnvironment["ATLAS_TEST_SHARE_TEXT"] = text
        try await TestAuthHelper.launchSignedIn(app)
    }
}
