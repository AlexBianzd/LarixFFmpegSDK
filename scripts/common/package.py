"""Deterministic ZIP creation and fail-closed clean extraction."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

from scripts.common.release_manifest import (
    _validate_relative_path,
    _walk_regular_files,
    verify_release_metadata,
)


_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MAX_ARCHIVE_FILES = 100_000
_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024


def create_zip_package(sdk_root: Path, archive_path: Path) -> Path:
    """Create one normalized deterministic ZIP after verifying the staged SDK."""
    verify_release_metadata(sdk_root)
    files = _walk_regular_files(sdk_root, include_generated=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{archive_path.name}.", suffix=".tmp", dir=archive_path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(
            temporary, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in files:
                relative = path.relative_to(sdk_root).as_posix()
                info = zipfile.ZipInfo(relative, date_time=_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                mode = 0o755 if relative.startswith("bin/") and path.suffix.lower() == ".exe" else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.flag_bits = 0x800
                with path.open("rb") as source, archive.open(info, "w") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        os.replace(temporary, archive_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return archive_path


def _validated_zip_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries: list[zipfile.ZipInfo] = []
    seen: set[str] = set()
    portable_seen: set[str] = set()
    total_size = 0
    for info in archive.infolist():
        if len(entries) >= _MAX_ARCHIVE_FILES:
            raise ValueError("SDK archive exceeds the file-count limit")
        name = _validate_relative_path(info.filename)
        portable = name.casefold()
        if name in seen or portable in portable_seen:
            raise ValueError("SDK archive contains a duplicate path")
        seen.add(name)
        portable_seen.add(portable)
        if info.is_dir() or name.endswith("/"):
            raise ValueError("SDK archive contains an unexpected directory entry")
        unix_mode = info.external_attr >> 16
        if stat.S_ISLNK(unix_mode) or (unix_mode and not stat.S_ISREG(unix_mode)):
            raise ValueError("SDK archive contains a link or special entry")
        if info.flag_bits & 1:
            raise ValueError("encrypted SDK archives are prohibited")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("unsupported SDK archive compression")
        total_size += info.file_size
        if total_size > _MAX_EXTRACTED_BYTES:
            raise ValueError("SDK archive exceeds the extraction size limit")
        entries.append(info)
    if not entries:
        raise ValueError("SDK archive is empty")
    return entries


def extract_zip_package(archive_path: Path, destination: Path) -> Path:
    """Safely extract an SDK ZIP into a new destination and verify its metadata."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("SDK extraction destination must not exist")
    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            entries = _validated_zip_entries(archive)
            destination.mkdir(parents=True)
            for info in entries:
                output_path = destination.joinpath(*info.filename.split("/"))
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, output_path.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                mode = info.external_attr >> 16
                os.chmod(output_path, stat.S_IMODE(mode) if mode else 0o644)
        verify_release_metadata(destination)
    except Exception:
        if destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination)
        raise
    return destination


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-root', type=Path)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--extract-to', type=Path)
    arguments = parser.parse_args()
    if (arguments.sdk_root is None) == (arguments.extract_to is None):
        parser.error('provide exactly one of --sdk-root or --extract-to')
    if arguments.sdk_root is not None:
        create_zip_package(arguments.sdk_root, arguments.archive)
    else:
        extract_zip_package(arguments.archive, arguments.extract_to)
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
