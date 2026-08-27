import UserNotifications

/// Without this, iOS silently suppresses a local notification's banner/sound
/// whenever the app is in the foreground -- exactly the moment right after
/// asking Atlas for a reminder in chat, which would make a "successfully
/// scheduled" reminder look like it never fired. A singleton because
/// `UNUserNotificationCenter.delegate` holds a weak reference; anything
/// shorter-lived would silently stop working.
final class NotificationPresenter: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationPresenter()

    private override init() {}

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound, .list])
    }
}
