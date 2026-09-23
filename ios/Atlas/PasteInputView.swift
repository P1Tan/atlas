import SwiftUI
import UIKit

/// Events-from-email screen. There is deliberately no editable text box any
/// more: text reaches extraction either from Gmail (the button below) or from
/// another app via the share sheet (AtlasShareExtension -> `ShareInbox` ->
/// `consumePendingShareText` -> `/extract`), which is the same code path the
/// box used to drive.
struct PasteInputView: View {
    @StateObject private var viewModel = ExtractionViewModel()
    @State private var calendarWriter = CalendarWriter()
    @EnvironmentObject private var shareInbox: ShareInbox
    @EnvironmentObject private var authViewModel: AuthViewModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var showingGmailConsent = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                gmailSection

                // Extraction used to be started by a button the user was
                // looking at, so its spinner lived in that button's label.
                // A share hand-off starts it with no tap at all, so the
                // progress has to be somewhere unconditional or the screen
                // would look inert for the whole round trip.
                if viewModel.isLoading {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("Extracting events…")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.top, 8)
                    .accessibilityIdentifier("ExtractLoadingIndicator")
                }

                if let errorMessage = viewModel.errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .font(.footnote)
                        .accessibilityIdentifier("ExtractErrorMessage")
                }

                resultsSection
            }
            .padding()
        }
        // Same interactive drag-to-dismiss the chat tab uses -- the event
        // rows are full of text fields, and a keyboard that only closes via
        // a return key that these fields don't have is a real dead end.
        .scrollDismissesKeyboard(.interactively)
        .onChange(of: shareInbox.pendingText) { _, newValue in
            consumePendingShareText(newValue)
        }
        .task {
            // Hosting this view inside a TabView means it isn't always
            // mounted at the moment a share hand-off arrives -- onChange
            // alone would miss a value that was already set before this
            // view appeared, so also check on appear.
            consumePendingShareText(shareInbox.pendingText)
            await viewModel.refreshGmailStatus(accessToken: await authViewModel.currentAccessToken())
        }
        // Still the thing that flips "Connect Gmail" to "Check Gmail" after
        // the user finishes consent in Safari and comes back -- the status
        // call just needs the token now, and fetching one is async (it may
        // refresh an expired session), which the Task this already had
        // absorbs.
        .onChange(of: scenePhase) { _, newPhase in
            guard newPhase == .active else { return }
            Task { await viewModel.refreshGmailStatus(accessToken: await authViewModel.currentAccessToken()) }
        }
        .alert("Connect Gmail?", isPresented: $showingGmailConsent) {
            Button("Cancel", role: .cancel) {}
            Button("Continue to Google Sign-In") {
                // The consent URL can't be built here any more: `/login` is
                // gone, and its replacement is an authenticated POST whose
                // whole point is that the backend learns which user this
                // login belongs to before Google is ever involved. So the
                // browser open waits on a round trip.
                Task {
                    if let url = await viewModel.startGmailConnect(
                        accessToken: await authViewModel.currentAccessToken()
                    ) {
                        // The async `open` overload, since this is now
                        // inside a Task -- the discarded Bool only reports
                        // whether iOS could hand the URL off, which says
                        // nothing about whether consent was completed.
                        _ = await UIApplication.shared.open(url)
                    }
                }
            }
        } message: {
            Text("Atlas will check your recent (last 30 days) unread email for events. Only short excerpts are sent to the AI model and shown to you for review — full email bodies are never stored or logged.")
        }
    }

    /// The share-sheet hand-off: text shared into Atlas from another app
    /// arrives here and is extracted immediately, with no intermediate
    /// editor to confirm it -- each result row shows its own
    /// `source_excerpt`, so the shared text stays visible where it matters.
    private func consumePendingShareText(_ text: String?) {
        guard let text else { return }
        shareInbox.pendingText = nil
        // `/extract` is authenticated now, so the token has to be fetched
        // before the call -- and fetching it is itself async (it may refresh
        // an expired session), which is why it happens inside the Task the
        // hand-off already needed rather than at the call site.
        Task { await viewModel.extract(text: text, accessToken: await authViewModel.currentAccessToken()) }
    }

    @ViewBuilder
    private var gmailSection: some View {
        if viewModel.gmailConnected {
            VStack(alignment: .leading, spacing: 4) {
                Button {
                    Task { await viewModel.checkGmail(accessToken: await authViewModel.currentAccessToken()) }
                } label: {
                    Text("Check Gmail (unread)")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(viewModel.isLoading)
                .accessibilityIdentifier("CheckGmailButton")

                Text("Only recent (last 30 days), unread mail you haven't already reviewed is checked. Full email bodies are never stored.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                // Without this the exclusion is invisible: a check that
                // skipped everything looks identical to an inbox with no
                // events in it, and there'd be no way back to mail the user
                // reviewed once and now wants again.
                if viewModel.skippedReviewedCount > 0 {
                    HStack(spacing: 6) {
                        Text(skippedReviewedText)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .accessibilityIdentifier("GmailSkippedReviewedLabel")

                        Button("Show them again") {
                            Task {
                                await viewModel.checkGmailIncludingReviewed(
                                    accessToken: await authViewModel.currentAccessToken()
                                )
                            }
                        }
                        .font(.caption2)
                        .buttonStyle(.plain)
                        .foregroundStyle(Color.accentColor)
                        .disabled(viewModel.isLoading)
                        .accessibilityIdentifier("GmailShowReviewedButton")
                    }
                }
            }
        } else {
            Button {
                showingGmailConsent = true
            } label: {
                Text("Connect Gmail")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("ConnectGmailButton")
        }
    }

    /// Found live (crash report Atlas-2026-09-20-174404.ips: EXC_BREAKPOINT,
    /// `Array._checkSubscript` <- `Binding.subscript.getter` <-
    /// `Switch.updateUIView`): `ForEach($viewModel.draftEvents)` hands each
    /// row an INDEX-based binding, and an index outlives the element it
    /// points at. The first Gmail check produced 2 rows; the recheck found
    /// everything already reviewed and set `draftEvents = []`, but a row
    /// still being torn down read `$draftEvents[1]` for its "Add end time"
    /// Toggle and trapped out of range. Any check returning FEWER events
    /// than the one before it does this -- the share/extract path included;
    /// it never surfaced before because rechecks used to return the same
    /// events every time. Keying the binding on the row's stable `id`
    /// instead makes an out-of-range read impossible: a lookup that misses
    /// yields the placeholder (the row is going away regardless) and a write
    /// for a vanished id is dropped rather than landing on whichever event
    /// has since slid into that index.
    private func binding(for id: DraftEvent.ID) -> Binding<DraftEvent> {
        Binding(
            get: { viewModel.draftEvents.first { $0.id == id } ?? Self.placeholderEvent },
            set: { newValue in
                guard let index = viewModel.draftEvents.firstIndex(where: { $0.id == id }) else { return }
                viewModel.draftEvents[index] = newValue
            }
        )
    }

    /// Stands in for a row whose event has already been removed, for the one
    /// or two SwiftUI update passes before that row is actually gone. Stable
    /// and constant on purpose -- a binding getter that fabricated a fresh
    /// value (or a `Date()`) on every read would churn the view tree.
    private static let placeholderEvent = DraftEvent(
        from: ExtractedEvent(
            title: "",
            datePhrase: "",
            resolvedStart: nil,
            resolvedEnd: nil,
            allDay: false,
            location: nil,
            notes: nil,
            sourceExcerpt: "",
            confidence: .low,
            ambiguities: [],
            needsConfirmation: false
        ),
        fallbackStart: Date(timeIntervalSince1970: 0)
    )

    private var skippedReviewedText: String {
        let count = viewModel.skippedReviewedCount
        return "Skipped \(count) email\(count == 1 ? "" : "s") you've already reviewed."
    }

    @ViewBuilder
    private var resultsSection: some View {
        if !viewModel.draftEvents.isEmpty {
            Text("Proposed events")
                .font(.headline)
                .padding(.top, 8)

            // A plain VStack, not a List -- List's own scrolling/sizing
            // fights with the outer ScrollView (this previously collapsed
            // to zero height when the keyboard reduced available space).
            LazyVStack(alignment: .leading, spacing: 12) {
                ForEach(viewModel.draftEvents) { event in
                    EditableEventRow(event: binding(for: event.id), calendarWriter: calendarWriter)
                    Divider()
                }
            }
            .accessibilityIdentifier("EventList")
        } else if viewModel.hasSearched && !viewModel.isLoading {
            // Two different nothings. A Gmail check that skipped every
            // message found no *new* mail, which is a normal, reassuring
            // outcome; "No events found." would read as though Atlas had
            // looked at the inbox and come up empty. The generic label keeps
            // its identifier for the share/extract path, which AtlasUITests
            // asserts on.
            if viewModel.skippedReviewedCount > 0 {
                Text("No new emails to review.")
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("NoNewEmailsLabel")
            } else {
                Text("No events found.")
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("NoEventsFoundLabel")
            }
        } else if !viewModel.isLoading {
            // Nothing has been extracted yet this session. Without the old
            // paste box the screen would otherwise be a lone Gmail button
            // with no hint that sharing text into Atlas is the other way in.
            Text("Check Gmail, or share text to Atlas from another app to extract events.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 8)
                .accessibilityIdentifier("ExtractEmptyState")
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
            case .alreadyOnCalendar:
                Label("Already on Calendar", systemImage: "calendar.badge.checkmark")
                    .frame(maxWidth: .infinity)
            }
        }
        .buttonStyle(.bordered)
        // Green reads as "you did that just now," which isn't what happened
        // here -- the neutral tint says the event is accounted for without
        // claiming credit for a write that never took place.
        .tint(addToCalendarTint)
        .disabled(
            event.writeStatus == .adding
                || event.writeStatus == .added
                || event.writeStatus == .alreadyOnCalendar
        )
        .accessibilityIdentifier("AddToCalendarButton")

        if case .failed(let message) = event.writeStatus {
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(.red)
                .accessibilityIdentifier("AddToCalendarError")
        }
    }

    private var addToCalendarTint: Color {
        switch event.writeStatus {
        case .added: return .green
        case .alreadyOnCalendar: return .secondary
        default: return .accentColor
        }
    }

    /// The single explicit confirmation point: nothing is written to the
    /// calendar until the user taps this button for this specific event.
    private func confirmAndWrite() async {
        event.writeStatus = .adding
        switch await calendarWriter.write(event) {
        case .success:
            event.writeStatus = .added
        case .alreadyExists:
            event.writeStatus = .alreadyOnCalendar
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
        .environmentObject(AuthViewModel())
}
