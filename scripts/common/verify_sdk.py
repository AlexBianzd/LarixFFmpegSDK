"""Relocate and verify one packaged Larix FFmpeg SDK."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from scripts.common.package import extract_package
from scripts.common.release_manifest import (
    _normalize_dependencies,
    _normalize_toolchain,
    verify_release_metadata,
)
from scripts.common.windows_toolchain import (
    discover_visual_studio_environment,
    require_matching_toolchain,
)


def _require_archive_identity(
    archive: Path, manifest: dict[str, object]
) -> None:
    asset_name = manifest.get("assetName")
    if not isinstance(asset_name, str) or archive.name != asset_name:
        raise ValueError("archive filename does not match the release manifest")


def _require_inspection_report(
    report: object, manifest: dict[str, object]
) -> None:
    if not isinstance(report, dict) or set(report) != {
        "runtimeDependencies", "toolchain"
    }:
        raise ValueError("inspection report fields are invalid")
    target = manifest.get("target")
    if not isinstance(target, dict):
        raise ValueError("inspection target identity is invalid")
    observed = _normalize_dependencies(report["runtimeDependencies"], target)
    declared = _normalize_dependencies(manifest.get("runtimeDependencies"), target)
    if observed != declared:
        raise ValueError("inspected runtime dependencies do not match the manifest")
    declared_toolchain = manifest.get("toolchain")
    observed_toolchain = report.get("toolchain")
    if not isinstance(declared_toolchain, dict) or not isinstance(observed_toolchain, dict):
        raise ValueError("inspection toolchain identity is invalid")
    normalized_declared = _normalize_toolchain(declared_toolchain, target)
    normalized_observed = _normalize_toolchain(observed_toolchain, target)
    if target.get("platform") == "windows":
        require_matching_toolchain(normalized_declared, normalized_observed)
    elif normalized_declared != normalized_observed:
        raise ValueError("inspected toolchain identity does not match the manifest")


def _required_tool(
    name: str,
    environment: dict[str, str] | None = None,
    override: str | None = None,
) -> str:
    if override:
        return override
    variables = environment or os.environ
    executable = shutil.which(name, path=variables.get("PATH"))
    if executable is None:
        raise RuntimeError(f"required SDK verification tool is missing: {name}")
    return executable


def _duration_is(value: object, expected: float) -> bool:
    try:
        return abs(float(value) - expected) <= 0.000001
    except (TypeError, ValueError):
        return False


def verify_ffprobe_inputs(
    ffprobe: Path, video_path: Path, audio_path: Path, environment: dict[str, str]
) -> None:
    documents: list[dict[str, object]] = []
    for path in (video_path, audio_path):
        completed = subprocess.run(
            [ffprobe.as_posix(), '-v', 'error', '-count_frames',
             '-show_streams', '-show_format',
             '-of', 'json', str(path)],
            check=True, capture_output=True, text=True, env=environment)
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ValueError('packaged ffprobe returned invalid JSON') from error
        if not isinstance(value, dict):
            raise ValueError('packaged ffprobe result is not an object')
        documents.append(value)
    video, audio = documents
    video_streams = video.get('streams')
    audio_streams = audio.get('streams')
    if (
        not isinstance(video_streams, list) or len(video_streams) != 1
        or video_streams[0].get('codec_type') != 'video'
        or video_streams[0].get('codec_name') != 'rawvideo'
        or video_streams[0].get('width') != 2
        or video_streams[0].get('height') != 2
        or video_streams[0].get('pix_fmt') != 'bgr24'
        or video_streams[0].get('r_frame_rate') != '25/1'
        or video_streams[0].get('avg_frame_rate') != '25/1'
        or video_streams[0].get('time_base') != '1/25'
        or video_streams[0].get('nb_frames') not in (None, '1')
        or video_streams[0].get('nb_read_frames') != '1'
        or not _duration_is(video_streams[0].get('duration'), 0.04)
        or not isinstance(video.get('format'), dict)
        or video['format'].get('format_name') != 'avi'
        or not _duration_is(video['format'].get('duration'), 0.04)
    ):
        raise ValueError('packaged ffprobe video metadata is invalid')
    if (
        not isinstance(audio_streams, list) or len(audio_streams) != 1
        or audio_streams[0].get('codec_type') != 'audio'
        or audio_streams[0].get('codec_name') != 'pcm_s16le'
        or audio_streams[0].get('sample_rate') != '48000'
        or audio_streams[0].get('channels') != 1
        or float(audio_streams[0].get('duration', '0')) <= 0
        or not isinstance(audio.get('format'), dict)
        or audio['format'].get('format_name') != 'wav'
    ):
        raise ValueError('packaged ffprobe audio metadata is invalid')


def verify_sdk_archive(archive: Path, repo_root: Path) -> dict[str, object]:
    """Verify metadata, native dependencies, relocation, build, and runtime smoke."""
    with tempfile.TemporaryDirectory(prefix="larix-ffmpeg-sdk-verify-") as temporary:
        work = Path(temporary)
        sdk = extract_package(archive, work / "relocated-sdk")
        manifest = verify_release_metadata(sdk, repo_root)
        _require_archive_identity(archive, manifest)
        target = manifest.get("target")
        if not isinstance(target, dict):
            raise ValueError("archive target identity is invalid")
        target_id = target.get("id")
        environment = dict(os.environ)
        inspection = work / "inspection.json"
        if target_id == "windows-x64-msvc":
            environment, discovered = discover_visual_studio_environment()
            require_matching_toolchain(manifest["toolchain"], discovered)
            powershell = _required_tool("powershell", environment)
            inspection_command = [
                powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(repo_root / "scripts" / "platforms" / "windows" / "inspect.ps1"),
                "-SdkRoot", str(sdk),
                "-ExpectedPeCsv", ";".join(manifest["runtimeFiles"]),
                "-ReportPath", str(inspection),
            ]
            ffprobe = sdk / "bin" / "ffprobe.exe"
        elif target_id == "macos-arm64":
            if os.sys.platform != "darwin":
                raise RuntimeError("macOS SDK verification requires a macOS host")
            bash = _required_tool("bash", environment)
            macos_inspector = repo_root / "scripts/platforms/macos/inspect.sh"
            inspection_command = [
                bash, str(macos_inspector),
                "--sdk-root", str(sdk),
                "--expected-macho-csv", ";".join(manifest["runtimeFiles"]),
                "--report-path", str(inspection),
            ]
            ffprobe = sdk / "bin" / "ffprobe"
        else:
            raise ValueError("unsupported SDK verification target")
        subprocess.run(inspection_command, check=True, env=environment)
        try:
            report = json.loads(inspection.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("inspection report is invalid JSON") from error
        _require_inspection_report(report, manifest)
        fixtures = work / "fixtures"
        subprocess.run(
            [
                os.sys.executable,
                str(repo_root / "tests" / "fixtures" / "generate_media.py"),
                str(fixtures),
            ],
            check=True,
        )
        video = fixtures / "video.avi"
        audio = fixtures / "audio.wav"
        verify_ffprobe_inputs(ffprobe, video, audio, environment)
        cmake = _required_tool("cmake", environment, environment.get("LARIX_CMAKE"))
        build = work / "consumer-build"
        subprocess.run(
            [
                cmake, "-S", str(repo_root / "tests" / "consumer"), "-B", str(build),
                f"-DLarixFFmpegSDK_DIR={sdk / 'lib' / 'cmake' / 'LarixFFmpegSDK'}",
            ],
            check=True,
            env=environment,
        )
        subprocess.run(
            [cmake, "--build", str(build), "--config", "Release"],
            check=True,
            env=environment,
        )
        candidates = (
            build / "Release" / "larix_ffmpeg_smoke.exe",
            build / "larix_ffmpeg_smoke.exe",
            build / "larix_ffmpeg_smoke",
        )
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise RuntimeError("CMake consumer executable was not produced")
        environment = dict(environment)
        if target_id == "windows-x64-msvc":
            environment["PATH"] = str(sdk / "bin") + os.pathsep + environment.get("PATH", "")
        else:
            environment["DYLD_LIBRARY_PATH"] = str(sdk / "lib")
        subprocess.run([str(executable), str(video)], check=True, env=environment)
        return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    verify_sdk_archive(arguments.archive.resolve(), arguments.repo_root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
