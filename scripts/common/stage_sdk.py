"""Stage exact legal, build, configuration, and patch provenance."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


def _copy(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"required provenance file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def stage_legal_provenance(
    source_root: Path,
    repo_root: Path,
    stage_root: Path,
    profile: str,
    target_id: str = "windows-x64-msvc",
) -> None:
    """Copy the exact profile license and repository-controlled build inputs."""
    if profile not in {"lgpl", "gpl"}:
        raise ValueError("unknown FFmpeg profile")
    licenses = stage_root / "LICENSES"
    _copy(source_root / "LICENSE.md", licenses / "FFmpeg-LICENSE.md")
    primary = "COPYING.LGPLv2.1" if profile == "lgpl" else "COPYING.GPLv2"
    optional = "COPYING.LGPLv3" if profile == "lgpl" else "COPYING.GPLv3"
    _copy(source_root / primary, licenses / primary)
    if (source_root / optional).is_file():
        _copy(source_root / optional, licenses / optional)
    _copy(repo_root / "LICENSE", licenses / "LarixFFmpegSDK-MIT.txt")
    metadata = stage_root / "share" / "larix-ffmpeg-sdk"
    metadata.mkdir(parents=True, exist_ok=True)
    if target_id not in {"windows-x64-msvc", "macos-arm64"}:
        raise ValueError("unknown FFmpeg target")
    entry_point = (
        "scripts/build-windows.ps1"
        if target_id == "windows-x64-msvc"
        else "scripts/build-macos.sh"
    )
    (metadata / "BUILD.txt").write_text(
        f"Built reproducibly by {entry_point}.\n"
        "Exact configure arguments and toolchain identities are in build.json.\n",
        encoding="utf-8",
        newline="\n",
    )
    provenance = metadata / "provenance"
    config_files = (
        repo_root / "config" / "ffmpeg.lock.json",
        repo_root / "config" / "targets" / f"{target_id}.json",
        repo_root / "config" / "profiles" / "common.conf",
        repo_root / "config" / "profiles" / "lgpl.conf",
        repo_root / "config" / "profiles" / "gpl.conf",
    )
    for path in config_files:
        _copy(path, provenance / "config" / path.name)
    patch_root = repo_root / "patches" / "9.0.1"
    for path in sorted(patch_root.iterdir(), key=lambda item: item.name):
        _copy(path, provenance / "patches" / path.name)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--profile", choices=("lgpl", "gpl"), required=True)
    parser.add_argument(
        "--target",
        choices=("windows-x64-msvc", "macos-arm64"),
        default="windows-x64-msvc",
    )
    arguments = parser.parse_args()
    stage_legal_provenance(
        arguments.source_root.resolve(),
        arguments.repo_root.resolve(),
        arguments.stage_root.resolve(),
        arguments.profile,
        arguments.target,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
