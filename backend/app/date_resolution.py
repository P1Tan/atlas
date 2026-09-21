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

Matching convention for "this weekend" / bare "weekend": the coming
Saturday, except when the reference date is already a Saturday or Sunday,
in which case it's that same day -- an unmodified weekend phrase never
resolves into the past and never skips past the weekend already in
progress. "next weekend" always moves to a later Saturday.
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
_MONTH_NAMES_RE = re.compile(
    r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|"
    r"august|aug|september|sept|sep|october|oct|november|nov|december|dec)\b",
    re.IGNORECASE,
)
_WEEKEND_RE = re.compile(r"\b(?P<modifier>next|this)?\s*weekend\b", re.IGNORECASE)
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

    # Must precede the "next week" substring check below -- "next weekend"
    # contains "next week" as a literal substring, so without this it was
    # silently resolving to "a week from today" (same weekday) instead of
    # the coming Saturday.
    weekend_match = _WEEKEND_RE.search(lower)
    if weekend_match:
        modifier = (weekend_match.group("modifier") or "").lower()
        # Review finding F9(a): said DURING the weekend, an unmodified
        # "this weekend"/"weekend" means the weekend you're already in, so
        # it resolves to today (Saturday -> today, which the arithmetic
        # below already did; Sunday -> today, which it did NOT -- it
        # resolved to the Saturday six days out, i.e. next weekend). One
        # consistent rule, stated so it can be corrected rather than
        # guessed at: an unmodified weekend phrase never resolves into the
        # past and never skips the weekend in progress.
        if modifier != "next" and reference_date.weekday() in (5, 6):
            return reference_date
        days_ahead = (5 - reference_date.weekday()) % 7  # 5 = Saturday
        if modifier == "next":
            if days_ahead == 0:
                days_ahead = 7
        return reference_date + timedelta(days=days_ahead)

    # An explicit month name (found live: "December 3rd", "March 15th,
    # 2027") must be resolved by dateutil, which understands month+day(+year)
    # together -- checked BEFORE the bare-ordinal-day heuristic further down,
    # which would otherwise match just the "3rd"/"15th" part via
    # _ORDINAL_DAY_RE, discard the month (and any explicit year) entirely,
    # and substitute "nearest upcoming occurrence of that day-of-month"
    # instead -- silently resolving to the wrong month (sometimes the wrong
    # year) rather than surfacing the ambiguity, which is exactly what this
    # module's own docstring says it must never do.
    #
    # Review finding F9(b): committing to this branch on the REGEX match
    # alone was too eager. "may" is also an ordinary modal verb, so a
    # date_phrase like "may be Thursday" landed here, failed
    # dateutil(fuzzy=False), and returned None -- where the weekday branch
    # further down had resolved it to the upcoming Thursday before the
    # month-name branch existed. The branch is only taken now if dateutil
    # actually parses something; otherwise the remaining heuristics get
    # their turn.
    saw_month_name = bool(_MONTH_NAMES_RE.search(lower))
    if saw_month_name:
        try:
            parsed = dateutil_parser.parse(
                phrase.strip(), default=datetime.combine(reference_date, time.min), fuzzy=False
            )
            return parsed.date()
        except (ValueError, OverflowError):
            pass

    if "next month" in lower:
        # "the 3rd of next month" -- found live: this branch used to return
        # unconditionally (same day-of-month, one month out), discarding an
        # explicit ordinal day the same way the month-name case above did.
        ordinal_match = _ORDINAL_DAY_RE.search(lower)
        if ordinal_match:
            day = int(ordinal_match.group("day"))
            target_month = reference_date + relativedelta(months=1)
            last_day_of_month = (
                target_month.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
            ).day
            return target_month.replace(day=min(day, last_day_of_month))
        return reference_date + relativedelta(months=1)
    if "next week" in lower:
        return reference_date + timedelta(days=7)

    weekday_match = _WEEKDAY_RE.search(lower)
    if weekday_match:
        target = _WEEKDAYS[weekday_match.group("weekday").lower()]
        days_ahead = (target - reference_date.weekday()) % 7
        if weekday_match.group("modifier") and weekday_match.group("modifier").lower() == "next":
            if days_ahead == 0:
                days_ahead = 7
        return reference_date + timedelta(days=days_ahead)

    ordinal_match = _ORDINAL_DAY_RE.search(lower)
    if ordinal_match and not saw_month_name:
        # `not saw_month_name` keeps the fall-through above from quietly
        # undoing the reason the month-name branch is ordered first: for an
        # unparseable phrase that really does name a month ("Dec 3rd or
        # 4th"), matching the bare "3rd" here would drop "Dec" and resolve
        # to the 3rd of the nearest month instead -- a guess, and the wrong
        # one. Such a phrase stays unresolved, exactly as before this fix;
        # only the heuristics that can't silently swallow a month name
        # (weekday, "next week"/"next month") now get a turn after a failed
        # month parse.
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
