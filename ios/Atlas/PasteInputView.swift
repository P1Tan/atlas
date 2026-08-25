import SwiftUI
import UIKit

struct PasteInputView: View {
    @State private var emailText: String = ""
    @StateObject private var viewModel = ExtractionViewModel()
    @State private var calendarWriter = CalendarWriter()
    @EnvironmentObject private var shareInbox: ShareInbox
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Paste an email or message")
                .font(.headline)

            TextEditor(text: $emailText)
                .frame(minHeight: 140, maxHeight: 220)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.secondary.opacity(0.3))
                )
                .accessibilityIdentifier("EmailTextEditor")

            Button {
                Task { await viewModel.extract(text: emailText) }
            } label: {
                if viewModel.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                } else {
                    Text("Extract Events")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(
                emailText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || viewModel.isLoading
            )
            .accessibilityIdentifier("ExtractButton")

            gmailSection

            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .font(.footnote)
                    .accessibilityIdentifier("ExtractErrorMessage")
            }

            resultsSection

            Spacer()
        }
        .padding()
        .onChange(of: shareInbox.pendingText) { _, newValue in
            guard let text = newValue else { return }
            emailText = text
            shareInbox.pendingText = nil
            Task { await viewModel.extract(text: text) }
        }
        .task {
            await viewModel.refreshGmailStatus()
        }
        .onChange(of: scenePhase) { _, newPhase in
            guard newPhase == .active else { return }
            Task { await viewModel.refreshGmailStatus() }
        }
    }

    @ViewBuilder
    private var gmailSection: some View {
        if viewModel.gmailConnected {
            Button {
                Task { await viewModel.checkGmail() }
            } label: {
                Text("Check Gmail (unread)")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(viewModel.isLoading)
            .accessibilityIdentifier("CheckGmailButton")
        } else {
            Button {
                if let url = URL(string: "http://127.0.0.1:8000/auth/google/login") {
                    UIApplication.shared.open(url)
                }
            } label: {
                Text("Connect Gmail")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("ConnectGmailButton")
        }
    }

    @ViewBuilder
    private var resultsSection: some View {
        if !viewModel.draftEvents.isEmpty {
            Text("Proposed events")
                .font(.headline)
                .padding(.top, 8)

            List {
                ForEach($viewModel.draftEvents) { $event in
                    EditableEventRow(event: $event, calendarWriter: calendarWriter)
                }
            }
            .listStyle(.plain)
            .accessibilityIdentifier("EventList")
        } else if viewModel.hasSearched && !viewModel.isLoading {
            Text("No events found.")
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("NoEventsFoundLabel")
        }
    }
}

private struct EditableEventRow: View {
    @Binding var event: DraftEvent
    let calendarWriter: CalendarWriter

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("Title", text: $event.title)
                    .font(.subheadline.bold())
                    .accessibilityIdentifier("EventTitle")
                Spacer()
                ConfidenceBadge(confidence: event.confidence)
            }

            if let subject = event.sourceSubject {
                Label("Gmail: \(subject)", systemImage: "envelope")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("EventSourceSubject")
            }

            dateSection

            Toggle("All-day", isOn: $event.allDay)
                .font(.footnote)
                .accessibilityIdentifier("EventAllDayToggle")

            Toggle("Add end time", isOn: $event.hasEnd)
                .font(.footnote)
                .accessibilityIdentifier("EventHasEndToggle")

            if event.hasEnd {
                DatePicker(
                    "Ends", selection: $event.end,
                    displayedComponents: event.allDay ? [.date] : [.date, .hourAndMinute]
                )
                .font(.footnote)
                .accessibilityIdentifier("EventEndDatePicker")
            }

            HStack {
                Image(systemName: "mappin.and.ellipse")
                    .foregroundStyle(.secondary)
                TextField("Location", text: $event.location)
            }
            .font(.footnote)
            .accessibilityIdentifier("EventLocation")

            HStack(alignment: .top) {
                Image(systemName: "note.text")
                    .foregroundStyle(.secondary)
                TextField("Notes", text: $event.notes, axis: .vertical)
            }
            .font(.footnote)
            .accessibilityIdentifier("EventNotes")

            Divider()

            Text("From: \u{201C}\(event.sourceExcerpt)\u{201D}")
                .font(.caption)
                .foregroundStyle(.secondary)
                .italic()

            ForEach(event.ambiguities, id: \.self) { note in
                Label(note, systemImage: "questionmark.circle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            addToCalendarSection
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private var dateSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            DatePicker(
                "Starts", selection: $event.start,
                displayedComponents: event.allDay ? [.date] : [.date, .hourAndMinute]
            )
            .font(.footnote)
            .accessibilityIdentifier("EventStartDatePicker")

            if event.dateNeedsAttention {
                Label("Couldn't resolve a date from \u{201C}\(event.datePhrase)\u{201D} — please check this.", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .accessibilityIdentifier("EventDateWarning")
            }
        }
        .padding(8)
        .background(event.dateNeedsAttention ? Color.red.opacity(0.08) : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder
    private var addToCalendarSection: some View {
        Button {
            Task { await confirmAndWrite() }
        } label: {
            switch event.writeStatus {
            case .notAdded, .failed:
                Text(event.writeStatus == .notAdded ? "Add to Calendar" : "Retry Add to Calendar")
                    .frame(maxWidth: .infinity)
            case .adding:
                ProgressView()
                    .frame(maxWidth: .infinity)
            case .added:
                Label("Added to Calendar", systemImage: "checkmark.circle.fill")
                    .frame(maxWidth: .infinity)
            }
        }
        .buttonStyle(.bordered)
        .tint(event.writeStatus == .added ? .green : .accentColor)
        .disabled(event.writeStatus == .adding || event.writeStatus == .added)
        .accessibilityIdentifier("AddToCalendarButton")

        if case .failed(let message) = event.writeStatus {
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(.red)
                .accessibilityIdentifier("AddToCalendarError")
        }
    }

    /// The single explicit confirmation point: nothing is written to the
    /// calendar until the user taps this button for this specific event.
    private func confirmAndWrite() async {
        event.writeStatus = .adding
        switch await calendarWriter.write(event) {
        case .success:
            event.writeStatus = .added
        case .permissionDenied:
            event.writeStatus = .failed("Calendar access denied. Enable it in Settings > Atlas.")
        case .failure(let message):
            event.writeStatus = .failed(message)
        }
    }
}

private struct ConfidenceBadge: View {
    let confidence: Confidence

    var body: some View {
        Text(confidence.rawValue.capitalized)
            .font(.caption2.bold())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }

    private var color: Color {
        switch confidence {
        case .high: return .green
        case .medium: return .orange
        case .low: return .red
        }
    }
}

#Preview {
    PasteInputView()
        .environmentObject(ShareInbox())
}
