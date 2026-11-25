from __future__ import annotations

from typing import Any


ALLOWED = {"message", "status", "completed", "total", "error_code", "artifact_id"}


def event_payload(**values: Any) -> dict:
    return {key: value for key, value in values.items() if key in ALLOWED and value is not None}
