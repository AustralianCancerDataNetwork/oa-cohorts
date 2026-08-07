from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any

import sqlalchemy as sa
from orm_loader.helpers import normalise_null


def parse_bool(value: Any) -> bool | None:
    value = normalise_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "t", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "f", "0", "no", "n", "off"}:
            return False
    raise ValueError(f"Cannot coerce {value!r} to bool")


def parse_int(value: Any) -> int | None:
    value = normalise_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"Expected integer-compatible float, got {value!r}")
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            numeric = float(text)
            if not numeric.is_integer():
                raise ValueError(f"Expected integer-compatible string, got {value!r}")
            return int(numeric)
    raise ValueError(f"Cannot coerce {value!r} to int")


def parse_float(value: Any) -> float | None:
    value = normalise_null(value)
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        return float(value.strip())
    raise ValueError(f"Cannot coerce {value!r} to float")


def to_naive_utc(value: datetime) -> datetime:
    """
    Normalise a datetime to a naive value in UTC.

    Datetimes in this package are naive by policy. OMOP CDM datetime fields carry
    no timezone, and no column here uses ``DateTime(timezone=True)`` - an aware
    value would silently lose its offset on write to Postgres. Normalising on the
    way in keeps every datetime in one frame, so a source offset can never shift
    a value onto a different calendar day during downstream date comparisons.

    Naive input is returned unchanged.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def parse_datetime(value: Any) -> datetime | None:
    value = normalise_null(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_naive_utc(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        text = value.strip()
        try:
            return to_naive_utc(datetime.fromisoformat(text))
        except ValueError:
            return datetime.combine(date.fromisoformat(text), time.min)
    raise ValueError(f"Cannot coerce {value!r} to datetime")


def parse_date(value: Any) -> date | None:
    value = normalise_null(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        # Normalise before taking the date, so an offset cannot shift the day.
        return to_naive_utc(value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            # A full datetime string landing in a Date column: normalise the
            # offset before taking the day, mirroring parse_datetime. Raises
            # ValueError from fromisoformat if it is not a datetime either.
            return to_naive_utc(datetime.fromisoformat(text)).date()
    raise ValueError(f"Cannot coerce {value!r} to date")


def parse_enum(value: Any, enum_cls: type[Enum]) -> Any:
    value = normalise_null(value)
    if value is None or isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return enum_cls(text)
        except ValueError:
            for name, member in enum_cls.__members__.items():
                if text == name or text.lower() == name.lower():
                    return member
            if "_" in text:
                try:
                    return enum_cls(text.split("_", 1)[1])
                except ValueError:
                    pass
    return enum_cls(value)


def coerce_column_value(column: sa.Column[Any], value: Any) -> Any:
    value = normalise_null(value)
    if value is None:
        return None

    column_type = column.type
    if isinstance(column_type, sa.Enum):
        if column_type.enum_class is None:
            return value
        return parse_enum(value, column_type.enum_class)
    if isinstance(column_type, sa.Boolean):
        return parse_bool(value)
    if isinstance(column_type, sa.Integer):
        return parse_int(value)
    if isinstance(column_type, (sa.Float, sa.Numeric)):
        return parse_float(value)
    if isinstance(column_type, sa.DateTime):
        return parse_datetime(value)
    if isinstance(column_type, sa.Date):
        return parse_date(value)
    return value
