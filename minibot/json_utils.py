"""JSON file helpers with UTF-8 BOM compatibility."""

from __future__ import annotations

import json
from pathlib import Path


class JsonFileError(ValueError):
    """Raised when a JSON file cannot be loaded into structured data."""


def load_json_file(
    path: Path, *, missing_ok: bool = False, default: object | None = None
) -> object:
    """Load JSON using UTF-8 BOM-compatible decoding."""

    if not path.exists():
        if missing_ok:
            return default
        raise JsonFileError(f"JSON file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise JsonFileError(
            f"Invalid JSON config: {path} ({exc.msg} at line {exc.lineno} column {exc.colno})"
        ) from exc
