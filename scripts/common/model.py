"""Fail-closed model loading for the FFmpeg source lock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_EXPECTED = {
    "schemaVersion": 1,
    "upstreamVersion": "9.0.1",
    "packagingRevision": 1,
    "releaseTag": "ffmpeg-9.0.1-larix.1",
    "source": {
        "url": "https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz",
        "archive": "ffmpeg-9.0.1.tar.xz",
        "size": 12036420,
        "sha256": "cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635",
    },
    "profiles": ["lgpl", "gpl"],
}


def load_lock(path: Path) -> dict[str, object]:
    """Load one exact, UTF-8 FFmpeg source lock or fail closed."""
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid FFmpeg lock: {path}") from error

    if not isinstance(value, dict) or set(value) != set(_EXPECTED):
        raise ValueError("FFmpeg lock fields are invalid")
    if value["schemaVersion"] != _EXPECTED["schemaVersion"]:
        raise ValueError("FFmpeg lock schema version is invalid")
    if value["upstreamVersion"] != _EXPECTED["upstreamVersion"]:
        raise ValueError("FFmpeg lock upstream version is invalid")
    if value["packagingRevision"] != _EXPECTED["packagingRevision"]:
        raise ValueError("FFmpeg lock packaging revision is invalid")
    if value["releaseTag"] != _EXPECTED["releaseTag"]:
        raise ValueError("FFmpeg lock release tag is invalid")

    source = value["source"]
    if not isinstance(source, dict) or set(source) != set(_EXPECTED["source"]):
        raise ValueError("FFmpeg lock source fields are invalid")
    if source != _EXPECTED["source"]:
        raise ValueError("FFmpeg lock source identity is invalid")

    profiles = value["profiles"]
    if profiles != _EXPECTED["profiles"]:
        raise ValueError("FFmpeg lock profiles are invalid")

    return json.loads(json.dumps(value))
