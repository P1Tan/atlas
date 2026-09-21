from datetime import datetime

import pytest

from app.date_resolution import resolve_date_phrase

TZ = "America/New_York"
# Tuesday.
REFERENCE = datetime(2026, 8, 18, 12, 0, 0)


def test_bare_weekday_resolves_to_closest_upcoming_occurrence() -> None:
    result = resolve_date_phrase("Thursday", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-20"
    assert result.all_day is True
    assert result.start.tzinfo is not None


def test_next_weekday_skips_a_week_when_reference_is_that_weekday() -> None:
    thursday_reference = datetime(2026, 8, 20, 12, 0, 0)
    result = resolve_date_phrase("next Thursday", thursday_reference, TZ)
    assert result.start.date().isoformat() == "2026-08-27"


def test_next_weekday_matches_bare_weekday_when_not_today() -> None:
    result = resolve_date_phrase("next Thursday", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-20"


def test_weekday_with_time_sets_start_and_not_all_day() -> None:
    result = resolve_date_phrase("next Thursday at 3pm", REFERENCE, TZ)
    assert result.start.isoformat() == "2026-08-20T15:00:00-04:00"
    assert result.end is None
    assert result.all_day is False


def test_weekday_with_am_time() -> None:
    result = resolve_date_phrase("Monday at 9am", REFERENCE, TZ)
    assert result.start.isoformat() == "2026-08-24T09:00:00-04:00"


def test_next_week_adds_seven_days() -> None:
    result = resolve_date_phrase("next week", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-25"
    assert result.all_day is True


def test_tomorrow() -> None:
    result = resolve_date_phrase("tomorrow", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-19"


@pytest.mark.parametrize(
    "reference,expected",
    [
        # Ordinary month: "next month" is the same day next month.
        (datetime(2026, 8, 18, 12, 0, 0), "2026-09-18"),
        # Month-end edge: Jan 31 + 1 month must clip to Feb's last day, not
        # overflow into March.
        (datetime(2026, 1, 31, 12, 0, 0), "2026-02-28"),
    ],
)
def test_next_month_handles_month_end_edges(reference: datetime, expected: str) -> None:
    result = resolve_date_phrase("next month", reference, TZ)
    assert result.start.date().isoformat() == expected


def test_ordinal_day_rolls_to_next_month_if_already_passed() -> None:
    result = resolve_date_phrase("the 1st", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-09-01"


def test_ordinal_day_stays_in_current_month_if_upcoming() -> None:
    result = resolve_date_phrase("the 31st", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-31"


def test_ordinal_day_month_end_edge_skips_month_without_that_day() -> None:
    # Referenced from Jan 31: "the 30th" doesn't exist in Feb, so it must
    # roll forward to March, not silently pick an invalid or wrong date.
    reference = datetime(2026, 1, 31, 12, 0, 0)
    result = resolve_date_phrase("the 30th", reference, TZ)
    assert result.start.date().isoformat() == "2026-03-30"


def test_explicit_absolute_date() -> None:
    result = resolve_date_phrase("August 31", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-31"
    assert result.all_day is True


def test_bare_time_with_no_date_defaults_to_reference_date() -> None:
    result = resolve_date_phrase("noon", REFERENCE, TZ)
    assert result.start.isoformat() == "2026-08-18T12:00:00-04:00"
    assert result.all_day is False


def test_midnight_with_no_date_defaults_to_reference_date() -> None:
    result = resolve_date_phrase("midnight", REFERENCE, TZ)
    assert result.start.isoformat() == "2026-08-18T00:00:00-04:00"


@pytest.mark.parametrize("phrase", ["ASAP", "let me know", "sometime soon", "TBD"])
def test_unparseable_phrase_resolves_to_no_date_rather_than_a_guess(phrase: str) -> None:
    result = resolve_date_phrase(phrase, REFERENCE, TZ)
    assert result.start is None
    assert result.end is None
    assert result.all_day is False


def test_bare_hour_without_meridiem_is_not_guessed() -> None:
    # "at 3" alone is ambiguous (3am or 3pm?) -- must not silently assume one.
    result = resolve_date_phrase("Thursday at 3", REFERENCE, TZ)
    assert result.all_day is True
    assert result.start.time().isoformat() == "00:00:00"


def test_result_is_timezone_aware_in_the_requested_zone() -> None:
    result = resolve_date_phrase("tomorrow", REFERENCE, "Asia/Tokyo")
    assert result.start.utcoffset().total_seconds() == 9 * 3600


def test_unknown_timezone_raises_value_error() -> None:
    with pytest.raises(ValueError):
        resolve_date_phrase("tomorrow", REFERENCE, "Not/A_Zone")


def test_this_weekend_resolves_to_the_coming_saturday_midweek() -> None:
    # REFERENCE is a Tuesday.
    result = resolve_date_phrase("this weekend", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-22"


def test_this_weekend_on_a_saturday_resolves_to_today() -> None:
    saturday = datetime(2026, 8, 22, 12, 0, 0)
    result = resolve_date_phrase("this weekend", saturday, TZ)
    assert result.start.date().isoformat() == "2026-08-22"


def test_this_weekend_on_a_sunday_resolves_to_today_not_next_weekend() -> None:
    # Review finding F9(a): this used to resolve to the Saturday six days
    # out -- i.e. NEXT weekend -- for someone saying "this weekend" while
    # the weekend was still happening.
    sunday = datetime(2026, 8, 23, 12, 0, 0)
    result = resolve_date_phrase("this weekend", sunday, TZ)
    assert result.start.date().isoformat() == "2026-08-23"


def test_next_weekend_on_a_sunday_still_moves_forward() -> None:
    sunday = datetime(2026, 8, 23, 12, 0, 0)
    result = resolve_date_phrase("next weekend", sunday, TZ)
    assert result.start.date().isoformat() == "2026-08-29"


def test_modal_may_does_not_hijack_the_month_name_branch() -> None:
    # Review finding F9(b): "may" is a month name AND an ordinary modal
    # verb, so this phrase entered the month branch, failed
    # dateutil(fuzzy=False) and resolved to nothing at all -- losing the
    # weekday the phrase plainly contains.
    result = resolve_date_phrase("may be Thursday", REFERENCE, TZ)
    assert result.start.date().isoformat() == "2026-08-20"


def test_unparseable_phrase_with_a_real_month_name_still_resolves_to_nothing() -> None:
    # The flip side of F9(b)'s fall-through: a genuinely ambiguous phrase
    # that really does name a month must NOT fall through to the
    # bare-ordinal heuristic, which would drop "Dec" and resolve to the 3rd
    # of the nearest month -- the silent wrong-month guess the month branch
    # was ordered first to prevent.
    result = resolve_date_phrase("Dec 3rd or 4th", REFERENCE, TZ)
    assert result.start is None
