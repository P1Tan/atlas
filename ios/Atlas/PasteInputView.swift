import SwiftUI

struct PasteInputView: View {
    @State private var emailText: String = ""
    @StateObject private var viewModel = ExtractionViewModel()

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
    }

    @ViewBuilder
    private var resultsSection: some View {
        if !viewModel.draftEvents.isEmpty {
            Text("Proposed events")
                .font(.headline)
                .padding(.top, 8)

            List {
                ForEach($viewModel.draftEvents) { $event in
                    EditableEventRow(event: $event)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("Title", text: $event.title)
                    .font(.subheadline.bold())
                    .accessibilityIdentifier("EventTitle")
                Spacer()
                ConfidenceBadge(confidence: event.confidence)
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
}
