import Foundation
import UserNotifications

/// Schedules a one-time local notification. Always fires with a resolved
/// absolute time computed by backend code -- never a time the LLM invented itself.
final class ReminderScheduler {
    enum ReminderScheduleResult {
        case success
        case permissionDenied
        case failure(String)
    }

    /// Requests notification access if not yet determined. Never schedules
    /// anything itself -- callers decide when a schedule is actually confirmed.
    func requestAccessIfNeeded() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()

        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            return true
        case .notDetermined:
            return (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        default:
            return false
        }
    }

    /// Schedules exactly the reminder passed in. `triggerTimeISO8601` must
    /// already be a resolved absolute time -- this performs no date math itself.
    func schedule(title: String, triggerTimeISO8601: String) async -> ReminderScheduleResult {
        guard await requestAccessIfNeeded() else { return .permissionDenied }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let triggerDate = formatter.date(from: triggerTimeISO8601) else {
            return .failure("Could not parse trigger time: \(triggerTimeISO8601)")
        }

        let content = UNMutableNotificationContent()
        content.title = title
        content.body = "Reminder from Atlas"
        content.sound = .default

        let components = Calendar.current.dateComponents(
            [.year, .month, .day, .hour, .minute, .second],
            from: triggerDate
        )
        let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)

        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: trigger
        )

        return await withCheckedContinuation { continuation in
            UNUserNotificationCenter.current().add(request) { error in
                if let error {
                    continuation.resume(returning: .failure(error.localizedDescription))
                } else {
                    continuation.resume(returning: .success)
                }
            }
        }
    }
}
