import EventKit
import Foundation

@MainActor
final class CalendarWriter {
    enum WriteOutcome {
        case success
        case permissionDenied
        case failure(String)
    }

    private let eventStore = EKEventStore()

    /// Requests calendar access if not yet determined. Never writes anything
    /// itself -- callers decide when a write is actually confirmed.
    func requestAccessIfNeeded() async -> Bool {
        switch EKEventStore.authorizationStatus(for: .event) {
        case .fullAccess:
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

    private func resolvedEndDate(for draft: DraftEvent) -> Date {
        if draft.hasEnd {
            return draft.end
        }
        return draft.allDay ? draft.start : draft.start.addingTimeInterval(3600)
    }
}
