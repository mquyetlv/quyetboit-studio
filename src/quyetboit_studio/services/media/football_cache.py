"""Small checksum receipts for resumable football artifacts; no provider data."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ...core.models import CancellationToken, sha256_file


def atomic_text(path: Path, value: str, *, encoding: str = "utf-8") -> None:
    partial = path.with_name(f".{path.name}.partial")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        partial.write_text(value, encoding=encoding)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def cache_valid(path: Path, key: str, cancel: CancellationToken | None = None) -> bool:
    try:
        receipt = json.loads(path.with_suffix(path.suffix + ".json").read_text(encoding="utf-8"))
        return (
            receipt["key"] == key
            and path.stat().st_size == receipt["size"]
            and sha256_file(path, cancel) == receipt["checksum"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def cache_record(path: Path, key: str, cancel: CancellationToken | None = None) -> None:
    receipt = path.with_suffix(path.suffix + ".json")
    partial = receipt.with_suffix(".json.partial")
    try:
        partial.write_text(
            json.dumps(
                {"key": key, "size": path.stat().st_size, "checksum": sha256_file(path, cancel)}
            ),
            encoding="utf-8",
        )
        os.replace(partial, receipt)
    finally:
        partial.unlink(missing_ok=True)
