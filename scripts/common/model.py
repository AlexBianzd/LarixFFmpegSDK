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

_TARGET_REQUIRED_FIELDS = {
    "id",
    "platform",
    "architecture",
    "abi",
    "toolchain",
    "linkage",
    "packageFormat",
    "driver",
}
_TARGET_OPTIONAL_FIELDS = {"minimumOsVersion"}
_PROFILES = {"lgpl", "gpl"}
_DESKTOP_PLATFORMS = {"windows", "macos", "linux"}


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate FFmpeg lock field: {key}")
        result[key] = value
    return result


def load_lock(path: Path) -> dict[str, object]:
    """Load one exact, UTF-8 FFmpeg source lock or fail closed."""
    try:
        value: Any = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"invalid FFmpeg lock: {path}") from error

    if not isinstance(value, dict) or set(value) != set(_EXPECTED):
        raise ValueError("FFmpeg lock fields are invalid")
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != _EXPECTED["schemaVersion"]
    ):
        raise ValueError("FFmpeg lock schema version is invalid")
    if value["upstreamVersion"] != _EXPECTED["upstreamVersion"]:
        raise ValueError("FFmpeg lock upstream version is invalid")
    if (
        type(value["packagingRevision"]) is not int
        or value["packagingRevision"] != _EXPECTED["packagingRevision"]
    ):
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


def _load_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"invalid {description}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be an object")
    return value


def load_target(path: Path) -> dict[str, object]:
    """Load one strict target descriptor without restricting future platforms."""
    value = _load_json_object(path, "FFmpeg target")
    fields = set(value)
    if not _TARGET_REQUIRED_FIELDS <= fields:
        raise ValueError("FFmpeg target is missing required fields")
    if not fields <= _TARGET_REQUIRED_FIELDS | _TARGET_OPTIONAL_FIELDS:
        raise ValueError("FFmpeg target has unknown fields")
    if any(type(value[field]) is not str or not value[field] for field in fields):
        raise ValueError("FFmpeg target fields must be nonempty strings")

    platform = value["platform"]
    if platform in _DESKTOP_PLATFORMS and value["linkage"] != "shared":
        raise ValueError("desktop FFmpeg targets must use shared linkage")
    if platform == "macos":
        if value["architecture"] != "arm64":
            raise ValueError("macOS FFmpeg target must use arm64")
        if value.get("minimumOsVersion") != "12.0":
            raise ValueError("macOS FFmpeg deployment target must be 12.0")
    return json.loads(json.dumps(value))


def _read_profile_arguments(path: Path) -> tuple[str, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"invalid FFmpeg profile: {path}") from error
    if not text:
        return ()
    lines = text.splitlines()
    if any(not line.strip() or line.lstrip().startswith("#") for line in lines):
        raise ValueError("FFmpeg profile contains a blank or comment-only argument")
    arguments = tuple(line.strip() for line in lines)
    if any(not argument.startswith("--") for argument in arguments):
        raise ValueError("FFmpeg profile arguments must be configure flags")
    return arguments


def compose_configure_args(
    repo_root: Path, profile: str, target_id: str
) -> tuple[str, ...]:
    """Compose deterministic common/profile arguments for a known target file."""
    if profile not in _PROFILES:
        raise ValueError(f"unknown FFmpeg profile: {profile}")
    target_path = repo_root / "config" / "targets" / f"{target_id}.json"
    target = load_target(target_path)
    if target["id"] != target_id:
        raise ValueError("FFmpeg target ID does not match its file name")

    common = _read_profile_arguments(repo_root / "config" / "profiles" / "common.conf")
    specific = _read_profile_arguments(
        repo_root / "config" / "profiles" / f"{profile}.conf"
    )
    arguments = common + specific
    if len(arguments) != len(set(arguments)):
        raise ValueError("duplicate FFmpeg configure argument")
    if "--enable-nonfree" in arguments:
        raise ValueError("nonfree FFmpeg builds are prohibited")
    if profile == "lgpl" and "--enable-gpl" in arguments:
        raise ValueError("LGPL profile cannot enable GPL")
    if profile == "gpl" and arguments.count("--enable-gpl") != 1:
        raise ValueError("GPL profile must enable GPL exactly once")
    return arguments


def target_asset_name(
    lock: dict[str, object], profile: str, target: dict[str, object]
) -> str:
    """Return the immutable SDK archive name for one profile/target pair."""
    if profile not in _PROFILES or profile not in lock.get("profiles", []):
        raise ValueError("invalid FFmpeg asset profile")
    target_id = target.get("id")
    package_format = target.get("packageFormat")
    if not isinstance(target_id, str) or package_format not in {"zip", "tar.xz"}:
        raise ValueError("invalid FFmpeg asset target")
    tag = lock.get("releaseTag")
    if not isinstance(tag, str) or not tag.startswith("ffmpeg-"):
        raise ValueError("invalid FFmpeg release tag")
    return f"larix-ffmpeg-sdk-{tag.removeprefix('ffmpeg-')}-{profile}-{target_id}.{package_format}"
