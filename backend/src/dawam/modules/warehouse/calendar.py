"""The generated date and time dimensions (spec §6.9 "Generated tables", story 90b).

Pure: settings in, columns and rows out; nothing here reads the database. The date key is the
integer ``YYYYMMDD`` (a deliberate exception to "surrogate keys are meaningless"); the time key
is the integer ``HHMM``. Hijri attributes follow the Umm al-Qura calendar and are NULL for days
outside its table (1924 to 2077). A fiscal year is named for the calendar year it ends in, so a
year starting in July 2024 is fiscal year 2025; starting in January it equals the calendar year.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from hijridate import Gregorian

from .service import DateDimension

DATE_KEY = "date_key"
TIME_KEY = "time_key"

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class CalendarColumn:
    name: str
    data_type: Mapping[str, Any]
    is_nullable: bool = False
    role: str = "attribute"


def date_dimension_of(stored: Mapping[str, Any]) -> DateDimension:
    """The settings as stored on the Data Warehouse (a JSON object)."""
    return DateDimension(**{**stored, "weekend_days": tuple(stored["weekend_days"])})


def _int(name: str, *, nullable: bool = False, role: str = "attribute") -> CalendarColumn:
    return CalendarColumn(name, {"type": "integer"}, nullable, role)


def _text(name: str, length: int, *, nullable: bool = False) -> CalendarColumn:
    return CalendarColumn(name, {"type": "string", "length": length}, nullable)


def date_columns(settings: DateDimension) -> list[CalendarColumn]:
    """The date dimension's columns; Hijri and fiscal ones only when the settings ask."""
    columns = [
        _int(DATE_KEY, role="sk"),
        CalendarColumn("full_date", {"type": "date"}),
        _int("year"),
        _int("quarter"),
        _int("month"),
        _text("month_name", 12),
        _int("day_of_month"),
        _int("day_of_week"),
        _text("day_name", 12),
        _int("day_of_year"),
        _int("iso_week"),
        _int("year_month"),
        CalendarColumn("is_weekend", {"type": "boolean"}),
        CalendarColumn("is_month_end", {"type": "boolean"}),
    ]
    if settings.include_hijri:
        columns += [
            _int("hijri_year", nullable=True),
            _int("hijri_month", nullable=True),
            _int("hijri_day", nullable=True),
            _text("hijri_month_name", 20, nullable=True),
            _text("hijri_month_name_ar", 20, nullable=True),
            _int("hijri_year_month", nullable=True),
        ]
    if settings.fiscal_year_start_month is not None:
        columns += [_int("fiscal_year"), _int("fiscal_quarter"), _int("fiscal_month")]
    return columns


def time_columns() -> list[CalendarColumn]:
    return [
        _int(TIME_KEY, role="sk"),
        _int("hour_24"),
        _int("hour_12"),
        _int("minute"),
        _text("am_pm", 2),
        _text("day_period", 10),
        _text("time_label", 5),
    ]


def _hijri(day: date) -> dict[str, Any]:
    try:
        h = Gregorian(day.year, day.month, day.day).to_hijri()
    except (OverflowError, ValueError):
        return dict.fromkeys(
            (
                "hijri_year",
                "hijri_month",
                "hijri_day",
                "hijri_month_name",
                "hijri_month_name_ar",
                "hijri_year_month",
            )
        )
    return {
        "hijri_year": h.year,
        "hijri_month": h.month,
        "hijri_day": h.day,
        "hijri_month_name": h.month_name(),
        "hijri_month_name_ar": h.month_name("ar"),
        "hijri_year_month": h.year * 100 + h.month,
    }


def _fiscal(day: date, start_month: int) -> dict[str, Any]:
    month = (day.month - start_month) % 12 + 1
    year = day.year + 1 if start_month > 1 and day.month >= start_month else day.year
    return {"fiscal_year": year, "fiscal_quarter": (month - 1) // 3 + 1, "fiscal_month": month}


def date_rows(settings: DateDimension) -> list[dict[str, Any]]:
    """One row per day from 1 January of the start year to 31 December of the end year.
    Hijri and fiscal values are included for every row; a table picks the columns it has."""
    weekend = {_DAYS.index(d) for d in settings.weekend_days}
    fiscal_start = settings.fiscal_year_start_month or 1
    day, last = date(settings.start_year, 1, 1), date(settings.end_year, 12, 31)
    rows: list[dict[str, Any]] = []
    while day <= last:
        weekday = day.weekday()
        rows.append(
            {
                DATE_KEY: day.year * 10000 + day.month * 100 + day.day,
                "full_date": day,
                "year": day.year,
                "quarter": (day.month - 1) // 3 + 1,
                "month": day.month,
                "month_name": _MONTHS[day.month - 1],
                "day_of_month": day.day,
                "day_of_week": weekday + 1,
                "day_name": _DAYS[weekday].capitalize(),
                "day_of_year": day.timetuple().tm_yday,
                "iso_week": day.isocalendar().week,
                "year_month": day.year * 100 + day.month,
                "is_weekend": weekday in weekend,
                "is_month_end": (day + timedelta(days=1)).day == 1,
                **_hijri(day),
                **_fiscal(day, fiscal_start),
            }
        )
        day += timedelta(days=1)
    return rows


def _period(hour: int) -> str:
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def time_rows() -> list[dict[str, Any]]:
    """One row per minute of the day."""
    return [
        {
            TIME_KEY: hour * 100 + minute,
            "hour_24": hour,
            "hour_12": hour % 12 or 12,
            "minute": minute,
            "am_pm": "AM" if hour < 12 else "PM",
            "day_period": _period(hour),
            "time_label": f"{hour:02d}:{minute:02d}",
        }
        for hour in range(24)
        for minute in range(60)
    ]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def seed_csv(columns: Sequence[CalendarColumn | str], rows: Sequence[Mapping[str, Any]]) -> str:
    """The seed file: a header, then ``rows`` limited to ``columns`` (empty cell = NULL)."""
    names = [c if isinstance(c, str) else c.name for c in columns]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(names)
    for row in rows:
        writer.writerow([_cell(row[n]) for n in names])
    return out.getvalue()
