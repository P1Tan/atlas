import XCTest

/// Requires the backend running locally at 127.0.0.1:8000 -- seeds a real fact
/// via a real /chat round trip (the `remember_fact` tool is the only way to
/// create one; there is no REST write path), then drives the real /facts
/// list and delete endpoints through the Memory tab UI.
@MainActor
final class MemoryUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testViewAndDeleteAMemory() async throws {
        let tokens = try await TestAuthHelper.fetchTestSessionTokens()

        // A short random suffix (not the full UUID) so the model has an easy,
        // unambiguous token to echo back verbatim into the stored fact,
        // rather than a long string it might be tempted to paraphrase or
        // truncate.
        let marker = "ATLAS-TEST-\(UUID().uuidString.prefix(8))"
        try await TestAuthHelper.sendChatMessage(
            "Please remember that my test marker is \(marker).",
            accessToken: tokens.accessToken
        )

        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)

        app.tabBars.buttons["Memory"].tap()

        let rows = app.staticTexts.matching(identifier: "MemoryFactRow")
        let markerRowPredicate = NSPredicate(format: "label CONTAINS %@", marker)
        let markerRow = rows.element(matching: markerRowPredicate)

        XCTAssertTrue(
            markerRow.waitForExistence(timeout: 10),
            "Expected a fact row containing the seeded marker '\(marker)' -- is the backend running on 127.0.0.1:8000?"
        )

        markerRow.swipeLeft()
        let deleteButton = app.buttons["Delete"]
        XCTAssertTrue(deleteButton.waitForExistence(timeout: 5))
        deleteButton.tap()

        let stillPresentPredicate = NSPredicate(format: "label CONTAINS %@", marker)
        let stillPresent = rows.element(matching: stillPresentPredicate)
        let goneExpectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: stillPresent
        )
        XCTAssertEqual(
            XCTWaiter().wait(for: [goneExpectation], timeout: 10),
            .completed,
            "Expected the deleted fact row to disappear from the list."
        )
    }

    /// Covers opening the edit sheet and its client-side validation only --
    /// deliberately not a save round trip, since the point of the disabled
    /// Save button is that a request the backend would answer with a 422
    /// never leaves the device. Also re-checks swipe-to-delete, which now
    /// shares the row with a tap gesture.
    func testEditSheetOpensPrefilledAndBlocksEmptySave() async throws {
        let tokens = try await TestAuthHelper.fetchTestSessionTokens()

        let marker = "ATLAS-TEST-\(UUID().uuidString.prefix(8))"
        try await TestAuthHelper.sendChatMessage(
            "Please remember that my test marker is \(marker).",
            accessToken: tokens.accessToken
        )

        let app = XCUIApplication()
        try await TestAuthHelper.launchSignedIn(app)

        app.tabBars.buttons["Memory"].tap()

        let rows = app.staticTexts.matching(identifier: "MemoryFactRow")
        let markerRow = rows.element(matching: NSPredicate(format: "label CONTAINS %@", marker))
        XCTAssertTrue(
            markerRow.waitForExistence(timeout: 10),
            "Expected a fact row containing the seeded marker '\(marker)' -- is the backend running on 127.0.0.1:8000?"
        )

        markerRow.tap()

        let editor = app.textViews["MemoryEditTextField"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        XCTAssertTrue(
            ((editor.value as? String) ?? "").contains(marker),
            "Expected the edit sheet to open prefilled with the fact's current text."
        )

        let saveButton = app.buttons["MemoryEditSaveButton"]
        XCTAssertTrue(saveButton.exists)
        XCTAssertTrue(saveButton.isEnabled, "Save should be available for the unmodified text.")

        // Clear the field: select-all then delete, with repeated backspaces
        // as a fallback, since the edit menu is timing-sensitive on the
        // Simulator and the fact's wording (the model's, not ours) has no
        // fixed length to count on.
        editor.tap()
        editor.press(forDuration: 1.0)
        if app.menuItems["Select All"].waitForExistence(timeout: 2) {
            app.menuItems["Select All"].tap()
        }
        editor.typeText(XCUIKeyboardKey.delete.rawValue)
        var attempts = 0
        while !((editor.value as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            attempts < 400
        {
            editor.typeText(XCUIKeyboardKey.delete.rawValue)
            attempts += 1
        }

        let disabledExpectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "isEnabled == false"), object: saveButton
        )
        XCTAssertEqual(
            XCTWaiter().wait(for: [disabledExpectation], timeout: 5),
            .completed,
            "Save should be disabled for empty text -- the backend answers 422 for it."
        )

        app.buttons["MemoryEditCancelButton"].tap()

        // Cancelling leaves the fact untouched...
        XCTAssertTrue(markerRow.waitForExistence(timeout: 5))

        // ...and the row still swipes to delete, which also cleans up the
        // fact this test seeded.
        markerRow.swipeLeft()
        let deleteButton = app.buttons["Delete"]
        XCTAssertTrue(deleteButton.waitForExistence(timeout: 5))
        deleteButton.tap()
    }
}
