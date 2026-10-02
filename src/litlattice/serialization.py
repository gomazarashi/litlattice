"""Convert Core results to JSON values and CLI envelopes.

Before v1.0, JSON output has no backward compatibility guarantee.
"""

import uuid
from collections.abc import Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from litlattice.errors import LitLatticeError


def to_json_value(value: Any) -> Any:
    """Convert Core data objects into JSON-compatible values.

    Field names are kept as they are; UUIDs, datetimes, Paths and Enums are
    converted to their canonical string forms.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    return value


def success_envelope(data: Any, warnings: Sequence[str] = ()) -> dict[str, Any]:
    """Build the envelope for a successful Core result."""
    return {
        "ok": True,
        "data": to_json_value(data),
        "warnings": list(warnings),
    }


def failure_envelope(error_type: str, message: str) -> dict[str, Any]:
    """Build the envelope for an expected failure from explicit details."""
    return {
        "ok": False,
        "error": {"type": error_type, "message": message},
    }


def error_envelope(error: LitLatticeError) -> dict[str, Any]:
    """Build the envelope for an expected Core failure."""
    return failure_envelope(type(error).__name__, str(error))
