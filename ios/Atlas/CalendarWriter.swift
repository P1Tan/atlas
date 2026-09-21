import EventKit
import Foundation

@MainActor
final class CalendarWriter {
    enum WriteOutcome {
        case success
        case permissionDenied
        /// The calendar already holds this event, so nothing was written.
        case alreadyExists
        case failure(String)
    }

    private let eventStore = EKEventStore()

    /// Requests calendar access if not yet determined. Never writes anything
    /// itself -- callers decide when a write is actually confirmed.
    ///
    /// Found live: the `default` branch used to treat `.writeOnly` the same
    /// as `.denied`/`.restricted`, so a user who deliberately granted
    /// exactly the minimal permission (Settings > Privacy & Security >
    /// Calendars > Atlas > "Add Only," available to any user regardless of
    /// what an app requests) got stuck on "Calendar access denied. Enable it
    /// in Settings," with no way to satisfy that prompt short of granting
    /// broader access. `.writeOnly` is therefore still accepted here.
    ///
    /// The reasoning behind what a *first-time* prompt asks for has since
    /// changed, though: `write(_:)` no longer only constructs and saves a
    /// new `EKEvent`, it first reads the calendar to check the event isn't
    /// already on it, and write-only authorization cannot read at all. So
    /// `.notDetermined` now asks for full access -- the permission the
    /// duplicate check actually needs, and the one the bundle's usage string
    /// (`NSCalendarsFullAccessUsageDescription`) already describes. A user
    /// who downgrades to "Add Only" anyway keeps a working Add button; they
    /// just silently lose the duplicate check (see `existingEvent(matching:)`),
    /// which is a best-effort guard, not a correctness requirement.
    func requestAccessIfNeeded() async -> Bool {
        switch EKEventStore.authorizationStatus(for: .event) {
        case .fullAccess, .writeOnly:
            return true
        case .notDetermined:
            return (try? await eventStore.requestFullAccessToEvents()) ?? false
        default:
            return false
        }
    }

    /// Writes exactly the event passed in. Callers must only invoke this in
    /// direct response to an explicit user confirmation -- never automatically.
    func write(_ draft: DraftEvent) async -> WriteOutcome {
        guard await requestAccessIfNeeded() else { return .permissionDenied }
        guard existingEvent(matching: draft) == nil else { return .alreadyExists }

        let event = EKEvent(eventStore: eventStore)
        event.title = draft.title
        event.startDate = draft.start
        event.isAllDay = draft.allDay
        event.endDate = resolvedEndDate(for: draft)
        event.location = draft.location.isEmpty ? nil : draft.location
        event.notes = draft.notes.isEmpty ? nil : draft.notes
        event.calendar = eventStore.defaultCalendarForNewEvents

        do {
            try eventStore.save(event, span: .thisEvent)
            return .success
        } catch {
            return .failure(error.localizedDescription)
        }
    }

    /// The same draft can reach this class more than once -- the user taps
    /// Add on a row, the next Gmail check re-offers the same message (or the
    /// same invitation arrives again as a forward/share), and nothing about
    /// a fresh `DraftEvent` remembers that it was written before. EventKit
    /// itself never deduplicates, so the calendar is the only durable record
    /// of what's already there and is what gets consulted here.
    ///
    /// Best effort by design: `.writeOnly` authorization cannot read the
    /// calendar at all, so there is nothing to compare against and the write
    /// proceeds exactly as it did before this guard existed -- a duplicate
    /// is a far better outcome than refusing to add anything for a user who
    /// chose "Add Only."
    ///
    /// The match is deliberately narrow (same trimmed, case-insensitive
    /// title AND essentially the same start) so that a genuinely recurring
    /// commitment -- standup at 9am every day, same title -- is never
    /// mistaken for a duplicate of yesterday's.
    private func existingEvent(matching draft: DraftEvent) -> EKEvent? {
        guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else { return nil }

        let title = draft.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }

        let predicate = eventStore.predicateForEvents(
            withStart: draft.start.addingTimeInterval(-60),
            end: draft.start.addingTimeInterval(60),
            calendars: nil
        )
        let calendar = Calendar.current

        return eventStore.events(matching: predicate).first { event in
            guard
                let existingTitle = event.title,
                existingTitle.trimmingCharacters(in: .whitespacesAndNewlines)
                    .caseInsensitiveCompare(title) == .orderedSame,
                let existingStart = event.startDate
            else { return false }

            // An all-day event's stored start is midnight in whatever time
            // zone it was created in, which is not necessarily the draft's
            // -- comparing timestamps would miss the duplicate it is meant
            // to catch, so all-day matching is by calendar day.
            if draft.allDay {
                return calendar.isDate(existingStart, inSameDayAs: draft.start)
            }
            return abs(existingStart.timeIntervalSince(draft.start)) <= 60
        }
    }

    private func resolvedEndDate(for draft: DraftEvent) -> Date {
        guard draft.hasEnd else {
            return draft.allDay ? draft.start : draft.start.addingTimeInterval(3600)
        }
        // Defense in depth alongside DraftEvent.start's own didSet (which
        // keeps `end` shifted in sync with `start` edits): this is the
        // actual point where an inverted interval would reach EventKit, so
        // it's clamped here too regardless of how draft.end could still end
        // up earlier than draft.start (e.g. a direct edit to "Ends" itself).
        return max(draft.end, draft.start)
    }
}
