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
        if !viewModel.events.isEmpty {
            Text("Proposed events")
                .font(.headline)
                .padding(.top, 8)

            List(viewModel.events) { event in
                EventRow(event: event)
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

private struct EventRow: View {
    let event: ExtractedEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(event.title)
                    .font(.subheadline.bold())
                    .accessibilityIdentifier("EventTitle")
                Spacer()
                ConfidenceBadge(confidence: event.confidence)
            }

            Text(dateSummary)
                .font(.footnote)
                .foregroundStyle(.secondary)

            if let location = event.location {
                Label(location, systemImage: "mappin.and.ellipse")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            ForEach(event.ambiguities, id: \.self) { note in
                Label(note, systemImage: "questionmark.circle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 4)
    }

    private var dateSummary: String {
        guard let start = event.resolvedStart else {
            return "Unresolved date: \(event.datePhrase)"
        }
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = event.allDay ? .none : .short
        return formatter.string(from: start)
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
