"""Deterministic resolution of a date_phrase (as extracted by the LLM) to an
absolute datetime, anchored to a reference datetime + timezone.

Never guesses: a phrase this module can't confidently resolve resolves to
(None, None, False) rather than an invented date, per the "ambiguity is
surfaced, not guessed" invariant.

Convention for "next <weekday>" / bare "<weekday>": both resolve to the
closest upcoming occurrence of that weekday (today counts for the bare form
only). This matches everyday scheduling usage ("let's meet next Thursday"
said on a Tuesday means the Thursday two days away), not the "skip a week"
reading some dialects use. Since every event is reviewed and confirmed by
the user before being written (confirm-before-write invariant), a
convention-driven day is easily corrected rather than silently wrong.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tues": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thurs": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_WEEKDAY_RE = re.compile(
    r"\b(?P<modifier>next|this)?\s*(?P<weekday>"
    + "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
_ORDINAL_DAY_RE = re.compile(r"\b(?:the\s+)?(?P<day>\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)
_NOON_RE = re.compile(r"\bnoon\b", re.IGNORECASE)
_MIDNIGHT_RE = re.compile(r"\bmidnight\b", re.IGNORECASE)
_TIME_WITH_MERIDIEM_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(:(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)\b", re.IGNORECASE
)
_TIME_24H_RE = re.compile(r"\b(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)\b")


@dataclass
class ResolvedDates:
    start: Optional[datetime]
    end: Optional[datetime]
    all_day: bool


def resolve_date_phrase(
    date_phrase: str, reference_datetime: datetime, timezone: str
) -> ResolvedDates:
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc

    reference = _as_aware(reference_datetime, tz)

    resolved_time, remaining_phrase = _extract_time(date_phrase)
    if resolved_time is not None and _is_filler_only(remaining_phrase):
        # e.g. "noon" or "at 3pm" alone, with no date phrase left over --
        # take that as "today at that time" rather than unresolved.
        resolved_date: Optional[date] = reference.date()
    else:
        resolved_date = _resolve_date_part(remaining_phrase, reference.date())

    if resolved_date is None:
        return ResolvedDates(start=None, end=None, all_day=False)

    if resolved_time is None:
        start = datetime.combine(resolved_date, time.min, tzinfo=tz)
        return ResolvedDates(start=start, end=None, all_day=True)

    start = datetime.combine(resolved_date, resolved_time, tzinfo=tz)
    return ResolvedDates(start=start, end=None, all_day=False)


_FILLER_ONLY_RE = re.compile(r"^\s*(at|on|by|around|about)?\s*$", re.IGNORECASE)


def _is_filler_only(phrase: str) -> bool:
    return bool(_FILLER_ONLY_RE.match(phrase))


def _as_aware(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _extract_time(phrase: str) -> tuple[Optional[time], str]:
    if _NOON_RE.search(phrase):
        return time(12, 0), _NOON_RE.sub(" ", phrase)
    if _MIDNIGHT_RE.search(phrase):
        return time(0, 0), _MIDNIGHT_RE.sub(" ", phrase)

    match = _TIME_WITH_MERIDIEM_RE.search(phrase)
    if match:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        meridiem = match.group("meridiem").lower()
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute), phrase[: match.start()] + " " + phrase[match.end() :]

    match = _TIME_24H_RE.search(phrase)
    if match:
        hour, minute = int(match.group("hour")), int(match.group("minute"))
        return time(hour, minute), phrase[: match.start()] + " " + phrase[match.end() :]

    # A bare hour with no am/pm/24h-colon is genuinely ambiguous -- treat the
    # phrase as date-only rather than guess a meridiem.
    return None, phrase


def _resolve_date_part(phrase: str, reference_date: date) -> Optional[date]:
    lower = phrase.lower()

    if "tomorrow" in lower:
        return reference_date + timedelta(days=1)
    if "today" in lower or "tonight" in lower:
        return reference_date
    if "next week" in lower:
        return reference_date + timedelta(days=7)
    if "next month" in lower:
        return reference_date + relativedelta(months=1)

    weekday_match = _WEEKDAY_RE.search(lower)
    if weekday_match:
        target = _WEEKDAYS[weekday_match.group("weekday").lower()]
        days_ahead = (target - reference_date.weekday()) % 7
        if weekday_match.group("modifier") and weekday_match.group("modifier").lower() == "next":
            if days_ahead == 0:
                days_ahead = 7
        return reference_date + timedelta(days=days_ahead)

    ordinal_match = _ORDINAL_DAY_RE.search(lower)
    if ordinal_match:
        day = int(ordinal_match.group("day"))
        return _next_occurrence_of_day(reference_date, day)

    try:
        parsed = dateutil_parser.parse(
            phrase.strip(), default=datetime.combine(reference_date, time.min), fuzzy=False
        )
        return parsed.date()
    except (ValueError, OverflowError):
        return None


def _next_occurrence_of_day(reference_date: date, day: int) -> Optional[date]:
    for months_ahead in (0, 1, 2):
        candidate_month = reference_date + relativedelta(months=months_ahead)
        last_day_of_month = (
            candidate_month.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
        ).day
        if day > last_day_of_month:
            continue
        candidate = candidate_month.replace(day=day)
        if candidate >= reference_date:
            return candidate
    return None
