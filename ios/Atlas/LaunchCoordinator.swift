import Foundation

/// Milestone 8.1 (FR11, "launch-to-listen"): tracks whether this app process
/// has already auto-started a voice turn on cold launch, so opening the app
/// drops straight into listening -- no in-app home screen, no mic button to
/// find first.
///
/// One-shot gate, not a reactive `@Published` trigger like `ShareInbox`'s
/// `pendingText` (that one reacts to a later external event -- a share-sheet
/// hand-off arriving after launch; this one just needs "was this already
/// done once this process lifetime," checked synchronously the first time
/// `ChatView` appears). Still an `ObservableObject` purely so it can be
/// threaded via `.environmentObject()` the same way as every other
/// app-wide dependency in this codebase.
@MainActor
final class LaunchCoordinator: ObservableObject {
    private var hasAutoStarted = false

    /// Returns `true` exactly once per app process launch. Every subsequent
    /// call -- including after backgrounding/foregrounding, or switching
    /// tabs away from and back to Chat -- returns `false`, so this only
    /// ever fires on a genuine cold open, not every time the user returns
    /// to the Chat tab.
    func consumeShouldAutoStartVoice() -> Bool {
        guard !hasAutoStarted else { return false }
        hasAutoStarted = true
        return true
    }
}
