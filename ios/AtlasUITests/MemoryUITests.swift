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
}
