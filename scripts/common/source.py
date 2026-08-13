"""Verified, fail-closed FFmpeg source acquisition and preparation."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any
import unicodedata
import urllib.request

from scripts.common.model import load_lock

_STATE_FILE = ".larix-source-state.json"
_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_PATH_LENGTH = 4_096
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *{f"com{number}" for number in range(1, 10)},
        *{f"lpt{number}" for number in range(1, 10)},
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)


@dataclass(frozen=True)
class _PatchSnapshot:
    name: str
    sha256: str
    content: bytes


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (is_junction is not None and is_junction())


def _portable_path_key(name: str) -> str:
    components = name.split("/")
    keys: list[str] = []
    for component in components:
        if (
            not component
            or component in {".", ".."}
            or len(component) > 255
            or component.endswith((" ", "."))
            or any(
                ord(character) < 32 or character in _WINDOWS_FORBIDDEN_CHARACTERS
                for character in component
            )
        ):
            raise ValueError("FFmpeg archive contains a nonportable path component")
        normalized = unicodedata.normalize("NFC", component)
        if normalized != component:
            raise ValueError("FFmpeg archive path is not Unicode-normalized")
        if component.split(".", maxsplit=1)[0].casefold() in _WINDOWS_RESERVED_NAMES:
            raise ValueError("FFmpeg archive path uses a reserved device name")
        keys.append(normalized.casefold())
    return "/".join(keys)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_patches(patch_dir: Path) -> tuple[_PatchSnapshot, ...]:
    if not patch_dir.is_dir():
        raise ValueError(f"patch directory is missing: {patch_dir}")
    snapshots: list[_PatchSnapshot] = []
    for path in sorted(patch_dir.glob("*.patch"), key=lambda item: item.name):
        if _is_link_like(path) or not path.is_file():
            raise ValueError(f"patch path is not a regular file: {path}")
        content = path.read_bytes()
        snapshots.append(
            _PatchSnapshot(path.name, hashlib.sha256(content).hexdigest(), content)
        )
    return tuple(snapshots)


def _patch_manifest(patches: tuple[_PatchSnapshot, ...]) -> list[dict[str, object]]:
    return [{"path": patch.name, "sha256": patch.sha256} for patch in patches]


def _state(
    lock: dict[str, object],
    patches: list[dict[str, object]],
    tree: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    source = lock.get("source")
    if not isinstance(source, dict):
        raise ValueError("FFmpeg lock has no source identity")
    result: dict[str, object] = {
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
    if tree is not None:
        result["tree"] = tree
    return result


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
    temporary: Path | None = None
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            final_url = response.geturl()
            if not isinstance(final_url, str) or not final_url.startswith("https://"):
                raise ValueError("FFmpeg source redirect is not HTTPS")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{archive.name}.download-",
                suffix=".tmp",
                dir=archive.parent,
                delete=False,
            ) as output:
                temporary = Path(output.name)
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
        if temporary is None:
            raise RuntimeError("FFmpeg source download temporary file was not created")
        os.replace(temporary, archive)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _verify_archive(source: dict[str, Any], archive: Path) -> None:
    if _is_link_like(archive) or not archive.is_file():
        raise ValueError("cached FFmpeg source archive is not a regular file")
    if archive.stat().st_size != source.get("size") or _sha256(archive) != source.get("sha256"):
        raise ValueError("cached FFmpeg source archive does not match the lock")


def _snapshot_verified_archive(
    source: dict[str, Any], archive: Path, snapshot: Path
) -> None:
    if _is_link_like(archive) or not archive.is_file():
        raise ValueError("FFmpeg source archive path is not a regular file")
    expected_size = source.get("size")
    expected_hash = source.get("sha256")
    digest = hashlib.sha256()
    size = 0
    with archive.open("rb") as input_stream, snapshot.open("xb") as output:
        while True:
            chunk = input_stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if type(expected_size) is not int or size > expected_size:
                raise ValueError("cached FFmpeg source archive exceeds the locked size")
            digest.update(chunk)
            output.write(chunk)
    if size != expected_size or digest.hexdigest() != expected_hash:
        raise ValueError("cached FFmpeg source archive does not match the lock")


def _validated_members(archive: tarfile.TarFile, expected_root: str) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    seen: set[str] = set()
    portable_seen: set[str] = set()
    total_size = 0
    separator = chr(92)
    for count, member in enumerate(archive, start=1):
        if count > _MAX_ARCHIVE_MEMBERS:
            raise ValueError("FFmpeg archive exceeds the member-count limit")
        name = member.name
        if (
            not name
            or len(name) > _MAX_ARCHIVE_PATH_LENGTH
            or separator in name
            or name.startswith(("/", separator))
        ):
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
        portable_key = _portable_path_key(name)
        if portable_key in portable_seen:
            raise ValueError("FFmpeg archive contains a nonportable path collision")
        portable_seen.add(portable_key)
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ValueError("FFmpeg archive contains an unsupported special member")
        if not member.isdir() and not member.isfile():
            raise ValueError("FFmpeg archive contains an unsupported member type")
        total_size += member.size
        if total_size > _MAX_EXTRACTED_BYTES:
            raise ValueError("FFmpeg archive exceeds the extraction size limit")
        members.append(member)
    if not members:
        raise ValueError("FFmpeg archive is empty")
    return members


def _extract(archive_path: Path, container: Path, expected_root: str) -> Path:
    with tarfile.open(archive_path, mode="r:xz") as archive:
        members = _validated_members(archive, expected_root)
        directory_modes: list[tuple[Path, int]] = []
        for member in members:
            destination = container.joinpath(*PurePosixPath(member.name).parts)
            mode = member.mode & 0o777
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                directory_modes.append((destination, mode))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("FFmpeg archive file has no payload")
            with source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(destination, mode)
        for destination, mode in sorted(
            directory_modes, key=lambda entry: len(entry[0].parts), reverse=True
        ):
            os.chmod(destination, mode)
    root = container / expected_root
    if _is_link_like(root) or not root.is_dir():
        raise ValueError("FFmpeg archive did not produce the expected source root")
    return root


def _apply_patches(root: Path, patches: tuple[_PatchSnapshot, ...]) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CEILING_DIRECTORIES": str(root.parent.resolve()),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    for patch in patches:
        subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.safecrlf=false",
                "-c",
                "apply.ignoreWhitespace=no",
                "apply",
                "--no-index",
                "--check",
                "--whitespace=nowarn",
                "-",
            ],
            cwd=root,
            check=True,
            env=environment,
            input=patch.content,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.safecrlf=false",
                "-c",
                "apply.ignoreWhitespace=no",
                "apply",
                "--no-index",
                "--whitespace=nowarn",
                "-",
            ],
            cwd=root,
            check=True,
            env=environment,
            input=patch.content,
        )


def _raise_walk_error(error: OSError) -> None:
    raise error


def _tree_manifest(root: Path) -> list[dict[str, object]]:
    if _is_link_like(root) or not root.is_dir():
        raise ValueError("verified FFmpeg source root is not a regular directory")
    result: list[dict[str, object]] = []
    for directory, directory_names, file_names in os.walk(
        root, topdown=True, onerror=_raise_walk_error, followlinks=False
    ):
        directory_names.sort()
        file_names.sort()
        current = Path(directory)
        for name in [*directory_names, *file_names]:
            path = current / name
            relative = path.relative_to(root).as_posix()
            if _is_link_like(path):
                raise ValueError("verified FFmpeg source tree contains a symbolic link")
            metadata = path.stat(follow_symlinks=False)
            entry: dict[str, object] = {
                "path": relative,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
            if stat.S_ISDIR(metadata.st_mode):
                entry["type"] = "directory"
            elif stat.S_ISREG(metadata.st_mode):
                entry.update(
                    {
                        "type": "file",
                        "size": metadata.st_size,
                        "sha256": _sha256(path),
                    }
                )
            else:
                raise ValueError("verified FFmpeg source tree contains a special file")
            result.append(entry)
    return result


def _verified_cached_tree(
    source_dir: Path,
    final_root: Path,
    state_path: Path,
    expected_state: dict[str, object],
) -> None:
    if (
        _is_link_like(source_dir)
        or _is_link_like(final_root)
        or _is_link_like(state_path)
        or not source_dir.is_dir()
        or not final_root.is_dir()
        or not state_path.is_file()
    ):
        raise ValueError("stale FFmpeg source destination")
    direct_members = sorted(path.name for path in source_dir.iterdir())
    if direct_members != sorted((_STATE_FILE, final_root.name)):
        raise ValueError("FFmpeg source destination does not contain exactly one root")
    try:
        current_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid FFmpeg source state") from error
    if not isinstance(current_state, dict):
        raise ValueError("invalid FFmpeg source state")
    tree = current_state.get("tree")
    base_state = {key: value for key, value in current_state.items() if key != "tree"}
    if base_state != expected_state or not isinstance(tree, list):
        raise ValueError("FFmpeg source state does not match lock or patches")
    if _tree_manifest(final_root) != tree:
        raise ValueError("verified FFmpeg source tree does not match its state")


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
    patch_snapshots = _snapshot_patches(patch_dir)
    patch_manifest = _patch_manifest(patch_snapshots)
    expected_state = _state(lock, patch_manifest)
    expected_root = f"ffmpeg-{version}"
    final_root = source_dir / expected_root
    state_path = source_dir / _STATE_FILE
    archive_path = download_dir / archive_name

    if _is_link_like(source_dir):
        raise ValueError("stale FFmpeg source destination")
    if source_dir.exists():
        _verified_cached_tree(source_dir, final_root, state_path, expected_state)
        _verify_archive(source, archive_path)
        return final_root

    if _is_link_like(archive_path):
        raise ValueError("FFmpeg source archive path is not a regular file")
    if archive_path.exists():
        if not archive_path.is_file():
            raise ValueError("FFmpeg source archive path is not a file")
    else:
        _download(source, archive_path)

    source_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{source_dir.name}-prepare-", dir=source_dir.parent))
    try:
        archive_snapshot = temporary / archive_name
        _snapshot_verified_archive(source, archive_path, archive_snapshot)
        staging = temporary / "source"
        staging.mkdir()
        root = _extract(archive_snapshot, staging, expected_root)
        _apply_patches(root, patch_snapshots)
        accepted_state = _state(lock, patch_manifest, _tree_manifest(root))
        (staging / _STATE_FILE).write_text(
            json.dumps(accepted_state, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, source_dir)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
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
