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

    /// Milestone 8.2 (FR11, home-screen widget): fired each time
    /// `atlas://listen` arrives (widget tap, whether that cold-launches the
    /// app or just foregrounds an already-running one). A `UUID`, not a
    /// `Bool`, because a second widget tap while the app is already running
    /// must still be observable as a new event -- a `Bool` that's already
    /// `true` wouldn't trigger `onChange` again.
    @Published var widgetVoiceTrigger: UUID?

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

    /// A cold launch via the widget also passes through
    /// `consumeShouldAutoStartVoice()` above (Chat is always the default
    /// tab), so this only does independent work when the app was already
    /// running -- `ChatViewModel.startVoiceTurn`'s own idle-state/
    /// reentrancy guards (Milestone 8.1) make firing both harmless even
    /// when they do overlap on a cold launch.
    func triggerVoiceFromWidget() {
        widgetVoiceTrigger = UUID()
    }
}
