import WidgetKit
import SwiftUI

/// Milestone 8.2 (FR11, home-screen widget): a one-tap mic button on the
/// home screen that deep-links straight into listening mode -- the whole
/// widget's surface is one `.widgetURL` tap target, not an interactive
/// widget Button/AppIntent, since starting a real voice turn needs the full
/// app (mic permission UI, a LiveKit connection) which can't run inside the
/// widget extension's own lightweight process.
struct AtlasVoiceWidgetEntry: TimelineEntry {
    let date: Date
}

/// The widget never has anything dynamic to show -- always the same mic
/// button -- so a single entry with `.never` as its reload policy is
/// correct; there's nothing a scheduled refresh would ever change.
struct AtlasVoiceWidgetProvider: TimelineProvider {
    func placeholder(in context: Context) -> AtlasVoiceWidgetEntry {
        AtlasVoiceWidgetEntry(date: Date())
    }

    func getSnapshot(in context: Context, completion: @escaping (AtlasVoiceWidgetEntry) -> Void) {
        completion(AtlasVoiceWidgetEntry(date: Date()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<AtlasVoiceWidgetEntry>) -> Void) {
        completion(Timeline(entries: [AtlasVoiceWidgetEntry(date: Date())], policy: .never))
    }
}

struct AtlasVoiceWidgetEntryView: View {
    var entry: AtlasVoiceWidgetProvider.Entry

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "mic.fill")
                .font(.system(size: 32, weight: .semibold))
                .foregroundStyle(.white)
            Text("Talk to Atlas")
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundStyle(.white)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .containerBackground(for: .widget) {
            // The same accentColor the in-app mic button uses for its own
            // "listening" affordance (see ChatView's VoiceMicButton) --
            // reusing that, not inventing a new brand color for this one
            // surface.
            LinearGradient(
                colors: [Color.accentColor, Color.accentColor.opacity(0.7)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
        .widgetURL(URL(string: "atlas://listen"))
    }
}

struct AtlasVoiceWidget: Widget {
    let kind: String = "AtlasVoiceWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: AtlasVoiceWidgetProvider()) { entry in
            AtlasVoiceWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("Talk to Atlas")
        .description("One tap to start talking to Atlas.")
        .supportedFamilies([.systemSmall])
    }
}

#Preview(as: .systemSmall) {
    AtlasVoiceWidget()
} timeline: {
    AtlasVoiceWidgetEntry(date: .now)
}
