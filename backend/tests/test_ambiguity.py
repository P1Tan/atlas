from app.ambiguity import finalize_confidence_and_ambiguities


def test_unresolved_date_forces_low_confidence_and_adds_note() -> None:
    confidence, ambiguities = finalize_confidence_and_ambiguities(
        confidence="high",
        ambiguities=[],
        date_resolved=False,
        date_phrase="sometime soon",
    )
    assert confidence == "low"
    assert any("sometime soon" in note for note in ambiguities)


def test_unresolved_date_does_not_duplicate_note_already_present() -> None:
    note = 'could not resolve a specific date from "sometime soon"'
    confidence, ambiguities = finalize_confidence_and_ambiguities(
        confidence="medium",
        ambiguities=[note],
        date_resolved=False,
        date_phrase="sometime soon",
    )
    assert ambiguities.count(note) == 1


def test_resolved_date_with_ambiguities_downgrades_high_to_medium() -> None:
    confidence, ambiguities = finalize_confidence_and_ambiguities(
        confidence="high",
        ambiguities=["year not specified"],
        date_resolved=True,
        date_phrase="next Thursday",
    )
    assert confidence == "medium"
    assert ambiguities == ["year not specified"]


def test_resolved_date_with_no_ambiguities_keeps_high_confidence() -> None:
    confidence, ambiguities = finalize_confidence_and_ambiguities(
        confidence="high",
        ambiguities=[],
        date_resolved=True,
        date_phrase="next Thursday",
    )
    assert confidence == "high"
    assert ambiguities == []


def test_resolved_date_never_upgrades_low_confidence() -> None:
    confidence, ambiguities = finalize_confidence_and_ambiguities(
        confidence="low",
        ambiguities=["vague location"],
        date_resolved=True,
        date_phrase="next Thursday",
    )
    assert confidence == "low"
