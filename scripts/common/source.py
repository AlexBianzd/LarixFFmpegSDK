"""Verified, fail-closed FFmpeg source acquisition and preparation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any
import urllib.request

from scripts.common.model import load_lock

_STATE_FILE = ".larix-source-state.json"
_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _patch_manifest(patch_dir: Path) -> list[dict[str, object]]:
    if not patch_dir.is_dir():
        raise ValueError(f"patch directory is missing: {patch_dir}")
    return [
        {"path": path.name, "sha256": _sha256(path)}
        for path in sorted(patch_dir.glob("*.patch"), key=lambda item: item.name)
        if path.is_file()
    ]


def _state(lock: dict[str, object], patches: list[dict[str, object]]) -> dict[str, object]:
    source = lock.get("source")
    if not isinstance(source, dict):
        raise ValueError("FFmpeg lock has no source identity")
    return {
        "schemaVersion": 1,
        "upstreamVersion": lock.get("upstreamVersion"),
        "source": {
            "url": source.get("url"),
            "archive": source.get("archive"),
            "size": source.get("size"),
            "sha256": source.get("sha256"),
        },
        "patches": patches,
    }


def _download(source: dict[str, Any], archive: Path) -> None:
    url = source.get("url")
    expected_size = source.get("size")
    expected_hash = source.get("sha256")
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or type(expected_size) is not int
        or expected_size <= 0
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
    ):
        raise ValueError("invalid locked FFmpeg source identity")
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(f".{archive.name}.download-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            final_url = response.geturl()
            if not isinstance(final_url, str) or not final_url.startswith("https://"):
                raise ValueError("FFmpeg source redirect is not HTTPS")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > expected_size:
                    raise ValueError("FFmpeg source download exceeds locked size")
                digest.update(chunk)
                output.write(chunk)
        if size != expected_size:
            raise ValueError("FFmpeg source download size mismatch")
        if digest.hexdigest() != expected_hash:
            raise ValueError("FFmpeg source download hash mismatch")
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_archive(source: dict[str, Any], archive: Path) -> None:
    if archive.stat().st_size != source.get("size") or _sha256(archive) != source.get("sha256"):
        raise ValueError("cached FFmpeg source archive does not match the lock")


def _validated_members(archive: tarfile.TarFile, expected_root: str) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    seen: set[str] = set()
    total_size = 0
    separator = chr(92)
    for member in members:
        name = member.name
        if not name or separator in name or name.startswith(("/", separator)):
            raise ValueError("FFmpeg archive contains an unsafe path")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("FFmpeg archive contains path traversal")
        if path.parts[0].endswith(":") or path.parts[0] != expected_root:
            raise ValueError("FFmpeg archive has an unexpected top-level root")
        normalized = path.as_posix()
        if normalized in seen:
            raise ValueError("FFmpeg archive contains a duplicate path")
        seen.add(normalized)
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ValueError("FFmpeg archive contains an unsupported special member")
        if not member.isdir() and not member.isfile():
            raise ValueError("FFmpeg archive contains an unsupported member type")
        total_size += member.size
        if total_size > _MAX_EXTRACTED_BYTES:
            raise ValueError("FFmpeg archive exceeds the extraction size limit")
    if not members:
        raise ValueError("FFmpeg archive is empty")
    return members


def _extract(archive_path: Path, container: Path, expected_root: str) -> Path:
    with tarfile.open(archive_path, mode="r:xz") as archive:
        members = _validated_members(archive, expected_root)
        for member in members:
            destination = container.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("FFmpeg archive file has no payload")
            with source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    root = container / expected_root
    if not root.is_dir():
        raise ValueError("FFmpeg archive did not produce the expected source root")
    return root


def _apply_patches(root: Path, patch_dir: Path, patches: list[dict[str, object]]) -> None:
    for entry in patches:
        patch = patch_dir / str(entry["path"])
        subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", str(patch)],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch)],
            cwd=root,
            check=True,
        )


def prepare_source(
    lock: dict[str, object], download_dir: Path, source_dir: Path, patch_dir: Path
) -> Path:
    """Return one verified and patched FFmpeg source root, or fail closed."""
    source = lock.get("source")
    version = lock.get("upstreamVersion")
    if not isinstance(source, dict) or not isinstance(version, str):
        raise ValueError("invalid FFmpeg lock for source preparation")
    archive_name = source.get("archive")
    if not isinstance(archive_name, str) or Path(archive_name).name != archive_name:
        raise ValueError("invalid FFmpeg source archive name")
    patches = _patch_manifest(patch_dir)
    expected_state = _state(lock, patches)
    expected_root = f"ffmpeg-{version}"
    final_root = source_dir / expected_root
    state_path = source_dir / _STATE_FILE
    archive_path = download_dir / archive_name

    if source_dir.exists():
        if not source_dir.is_dir() or not final_root.is_dir() or not state_path.is_file():
            raise ValueError("stale FFmpeg source destination")
        try:
            current_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid FFmpeg source state") from error
        if current_state != expected_state:
            raise ValueError("FFmpeg source state does not match lock or patches")
        if not archive_path.is_file():
            raise ValueError("verified FFmpeg source archive is missing")
        _verify_archive(source, archive_path)
        return final_root

    if archive_path.exists():
        if not archive_path.is_file():
            raise ValueError("FFmpeg source archive path is not a file")
        _verify_archive(source, archive_path)
    else:
        _download(source, archive_path)

    source_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{source_dir.name}-prepare-", dir=source_dir.parent))
    try:
        root = _extract(archive_path, temporary, expected_root)
        _apply_patches(root, patch_dir, patches)
        (temporary / _STATE_FILE).write_text(
            json.dumps(expected_state, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, source_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    repo_root = arguments.repo_root.resolve()
    output = arguments.output.resolve()
    lock = load_lock(repo_root / "config" / "ffmpeg.lock.json")
    root = prepare_source(
        lock,
        output / "downloads",
        output / "source",
        repo_root / "patches" / "9.0.1",
    )
    source = lock["source"]
    print(f"SHA256 {source['sha256']}")
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
