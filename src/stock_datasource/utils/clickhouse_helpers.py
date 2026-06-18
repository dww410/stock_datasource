"""Shared helper functions for ClickHouse query results.

Provides consistent NaN/serialization handling across services
that deal with ReplacingMergeTree query results.
"""

import math
from typing import Any


def to_records(rows: Any) -> list[dict[str, Any]]:
    """Convert ClickHouse query result to list of dicts."""
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        try:
            return rows.to_dict("records")
        except Exception:
            return []
    if isinstance(rows, list):
        return rows
    try:
        return list(rows)
    except Exception:
        return []


def is_nan_like(value: Any) -> bool:
    """Check if a value is None or NaN."""
    if value is None:
        return False
    if isinstance(value, float):
        try:
            return math.isnan(value)
        except Exception:
            return False
    try:
        return value != value
    except Exception:
        return False


def none_if_nan(value: Any) -> Any | None:
    """Return None if value is NaN, otherwise the value unchanged."""
    return None if is_nan_like(value) else value


def str_or_empty(value: Any) -> str:
    """Return string value or empty string if None/NaN."""
    if value is None or is_nan_like(value):
        return ""
    return str(value)
