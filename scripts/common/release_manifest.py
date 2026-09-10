"""Canonical, repository-bound FFmpeg SDK release metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from scripts.common.model import (
    compose_configure_args,
    load_lock,
    load_target,
    target_asset_name,
)
from scripts.common.release_schema import validate_release_manifest_schema


COMPONENTS = ("avutil", "avcodec", "avformat", "swresample", "swscale")
_DEFAULT_LOCK = load_lock(Path(__file__).resolve().parents[2] / "config/ffmpeg.lock.json")
LIBRARY_VERSIONS = dict(_DEFAULT_LOCK["libraryVersions"])
WINDOWS_RUNTIME_FILES = tuple(sorted(
    ["bin/ffprobe.exe"]
    + [
        f"bin/{component}-{LIBRARY_VERSIONS[component]}.dll"
        for component in COMPONENTS
    ]
))
WINDOWS_SYMBOL_FILES = tuple(
    "symbols/" + PurePosixPath(path).stem + ".pdb" for path in WINDOWS_RUNTIME_FILES
)
MACOS_RUNTIME_FILES = tuple(sorted(
    ["bin/ffprobe"]
    + [
        f"lib/lib{component}.{LIBRARY_VERSIONS[component]}.dylib"
        for component in COMPONENTS
    ]
))
WINDOWS_PLATFORM_CONFIGURE_ARGS = (
    "--toolchain=msvc",
    "--arch=x86_64",
    "--target-os=win64",
    "--cc=larix-msvc-cl.cmd",
    "--extra-cflags=/experimental:deterministic /Brepro /pathmap:./src=larix-source /pathmap:.=larix-build /pathmap:../install=larix-install /pathmap:..=larix-output",
    "--extra-ldflags=/Brepro /PDBALTPATH:%_PDB%",
    "--prefix=../install",
)
MACOS_PLATFORM_CONFIGURE_ARGS = (
    "--arch=arm64",
    "--target-os=darwin",
    "--cc=clang",
    "--install-name-dir=@rpath",
    "--extra-cflags=-mmacosx-version-min=12.0 -fdebug-compilation-dir=larix-build -ffile-prefix-map=${SOURCE}=larix-source -fdebug-prefix-map=${SOURCE}=larix-source -ffile-prefix-map=${BUILD}=larix-build -fdebug-prefix-map=${BUILD}=larix-build",
    "--extra-ldflags=-mmacosx-version-min=12.0 -Wl,-headerpad_max_install_names",
    "--prefix=../install",
)
# Retained as the public Windows contract used by existing Task 4 tests.
PLATFORM_CONFIGURE_ARGS = WINDOWS_PLATFORM_CONFIGURE_ARGS


def runtime_files_for_target(target_id: str) -> tuple[str, ...]:
    if target_id == "windows-x64-msvc":
        return WINDOWS_RUNTIME_FILES
    if target_id == "macos-arm64":
        return MACOS_RUNTIME_FILES
    raise ValueError(f"unsupported release target: {target_id}")


def symbol_files_for_target(target_id: str) -> tuple[str, ...]:
    if target_id == "windows-x64-msvc":
        return WINDOWS_SYMBOL_FILES
    if target_id == "macos-arm64":
        return ()
    raise ValueError(f"unsupported release target: {target_id}")


def platform_configure_args(target_id: str) -> tuple[str, ...]:
    if target_id == "windows-x64-msvc":
        return WINDOWS_PLATFORM_CONFIGURE_ARGS
    if target_id == "macos-arm64":
        return MACOS_PLATFORM_CONFIGURE_ARGS
    raise ValueError(f"unsupported release target: {target_id}")
_METADATA_DIRECTORY = PurePosixPath("share/larix-ffmpeg-sdk")
_GENERATED_METADATA = frozenset(
    {"manifest.json", "sbom.spdx.json", "SHA256SUMS"}
)
_FORBIDDEN_NAMES = frozenset(
    {"ffmpeg.exe", "ffplay.exe", "avfilter.lib", "avdevice.lib", "postproc.lib"}
)
_FORBIDDEN_COMPONENT_MARKERS = ("avfilter", "avdevice", "postproc")
_MAX_FILES = 100_000
_WINDOWS_RESERVED_NAMES = frozenset(
    {"aux", "clock$", "con", "conin$", "conout$", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _is_link_like(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (junction is not None and junction())


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError("SDK path is not a normalized relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("SDK path escapes the package root")
    if path.parts[0].endswith(":") or path.as_posix() != value:
        raise ValueError("SDK path is not a normalized relative path")
    forbidden = frozenset('<>:' + chr(34) + '|?*')
    for component in path.parts:
        if (
            component.endswith((" ", "."))
            or any(ord(character) < 32 or character in forbidden for character in component)
            or component.split(".", maxsplit=1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        ):
            raise ValueError("SDK path is not portable across package hosts")
    return value


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _walk_regular_files(root: Path, *, include_generated: bool) -> list[Path]:
    if _is_link_like(root) or not root.is_dir():
        raise ValueError("SDK root is not a regular directory")
    files: list[Path] = []

    def fail(error: OSError) -> None:
        raise error

    for directory, names, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=fail
    ):
        directory_path = Path(directory)
        for name in names:
            child = directory_path / name
            if _is_link_like(child) or not child.is_dir():
                raise ValueError(f"SDK tree contains a link or special path: {child}")
        for name in filenames:
            child = directory_path / name
            if _is_link_like(child):
                raise ValueError(f"SDK package contains a symlink: {child}")
            mode = child.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise ValueError(f"SDK package contains a special file: {child}")
            relative = child.relative_to(root).as_posix()
            _validate_relative_path(relative)
            if (
                not include_generated
                and PurePosixPath(relative).parent == _METADATA_DIRECTORY
                and PurePosixPath(relative).name in _GENERATED_METADATA
            ):
                continue
            files.append(child)
            if len(files) > _MAX_FILES:
                raise ValueError("SDK package exceeds the file-count limit")
    files.sort(key=lambda item: item.relative_to(root).as_posix())
    return files


def _inventory(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in _walk_regular_files(root, include_generated=False):
        size, digest = _hash_file(path)
        entries.append(
            {"path": path.relative_to(root).as_posix(), "sha256": digest, "size": size}
        )
    return entries


def _load_canonical_json(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_json_object_without_duplicates
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"invalid release metadata: {path}") from error
    if not isinstance(value, dict) or payload != _canonical_json(value):
        raise ValueError(f"release metadata is not canonical JSON: {path}")
    return value


def _load_build_info(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"build information is invalid JSON: {path}") from error
    if not isinstance(value, dict) or set(value) != {
        "forbiddenPaths", "runtimeDependencies", "toolchain"
    }:
        raise ValueError("build information fields are invalid")
    if (
        not isinstance(value["forbiddenPaths"], list)
        or not isinstance(value["runtimeDependencies"], dict)
        or not isinstance(value["toolchain"], dict)
    ):
        raise ValueError("build information values are invalid")
    return value


def _effective_license(profile: str) -> str:
    if profile == "lgpl":
        return "LGPL-2.1-or-later"
    if profile == "gpl":
        return "GPL-2.0-or-later"
    raise ValueError("unknown FFmpeg license profile")


def _required_license_files(profile: str) -> set[str]:
    result = {"FFmpeg-LICENSE.md", "LarixFFmpegSDK-MIT.txt"}
    result.add("COPYING.LGPLv2.1" if profile == "lgpl" else "COPYING.GPLv2")
    return result


def _patch_manifest(repo_root: Path) -> list[dict[str, object]]:
    root = repo_root / "patches" / "9.0.1"
    if not root.is_dir():
        raise ValueError("repository patch provenance is missing")
    result = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if _is_link_like(path) or not path.is_file():
            raise ValueError("repository patch provenance is not regular")
        if path.suffix != ".patch":
            continue
        _, digest = _hash_file(path)
        result.append({"path": path.name, "sha256": digest})
    return result


def _verify_staged_provenance(
    sdk_root: Path, repo_root: Path, target_id: str
) -> None:
    provenance = sdk_root / _METADATA_DIRECTORY.as_posix() / 'provenance'
    expected = {
        'config/ffmpeg.lock.json': repo_root / 'config' / 'ffmpeg.lock.json',
        f'config/{target_id}.json': repo_root / 'config' / 'targets' / f'{target_id}.json',
        'config/common.conf': repo_root / 'config' / 'profiles' / 'common.conf',
        'config/lgpl.conf': repo_root / 'config' / 'profiles' / 'lgpl.conf',
        'config/gpl.conf': repo_root / 'config' / 'profiles' / 'gpl.conf',
    }
    patch_root = repo_root / 'patches' / '9.0.1'
    for path in sorted(patch_root.iterdir(), key=lambda item: item.name):
        expected[f'patches/{path.name}'] = path
    actual = {
        path.relative_to(provenance).as_posix()
        for path in _walk_regular_files(provenance, include_generated=True)
    }
    if actual != set(expected):
        raise ValueError('staged config or patch provenance inventory is invalid')
    for relative, source in expected.items():
        if (provenance / relative).read_bytes() != source.read_bytes():
            raise ValueError('staged provenance does not match repository input: ' + relative)


def _normalize_toolchain(
    toolchain: dict[str, str], target: dict[str, object]
) -> dict[str, str]:
    platform = target.get("platform")
    expected = (
        {"compiler", "windowsSdk"}
        if platform == "windows"
        else {"compiler", "xcode", "macosSdk"}
        if platform == "macos"
        else set()
    )
    if not expected or set(toolchain) != expected:
        raise ValueError("toolchain identity fields are invalid")
    if any(not isinstance(value, str) or not value.strip() for value in toolchain.values()):
        raise ValueError("toolchain identity is incomplete")
    if platform == "windows" and "MSVC" not in toolchain["compiler"]:
        raise ValueError("Windows SDK compiler is not MSVC")
    if platform == "macos" and (
        "clang" not in toolchain["compiler"].casefold()
        or not toolchain["xcode"].startswith("Xcode ")
    ):
        raise ValueError("macOS SDK toolchain identity is invalid")
    return dict(toolchain)


def _normalize_dependencies(
    dependencies: dict[str, list[str]], target: dict[str, object]
) -> dict[str, list[str]]:
    runtime_files = runtime_files_for_target(str(target.get("id")))
    if set(dependencies) != set(runtime_files):
        raise ValueError("runtime dependency inventory is incomplete")
    result: dict[str, list[str]] = {}
    for path in runtime_files:
        values = dependencies[path]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
        ):
            raise ValueError("runtime dependency entry is invalid")
        if target.get("platform") == "windows":
            if any(not value.lower().endswith(".dll") for value in values):
                raise ValueError("runtime dependency entry is invalid")
            normalized = sorted({value.upper() for value in values})
        else:
            if any(
                not value.startswith(("@rpath/", "/usr/lib/", "/System/Library/Frameworks/"))
                for value in values
            ):
                raise ValueError("runtime dependency entry is invalid")
            normalized = sorted(set(values))
        if len(normalized) != len(values):
            raise ValueError("runtime dependency entry is duplicate or unsorted")
        result[path] = normalized
    return result


def _scan_forbidden_paths(root: Path, forbidden_paths: tuple[str, ...]) -> None:
    byte_needles: list[tuple[bytes, str]] = []
    text_needles: list[tuple[str, str]] = []
    for value in forbidden_paths:
        if not isinstance(value, str) or not value:
            raise ValueError("forbidden build path is invalid")
        variants = {value, value.replace("\\", "/"), value.replace("/", "\\")}
        for item in variants:
            if item:
                byte_needles.extend((
                    (item.encode("utf-8"), value),
                    (item.encode("utf-16le"), value),
                ))
                text_needles.append((item.casefold(), value))
    for path in _walk_regular_files(root, include_generated=True):
        if path.name in _GENERATED_METADATA:
            continue
        payload = path.read_bytes()
        for needle, forbidden in byte_needles:
            if needle in payload:
                raise ValueError(
                    f"SDK payload embeds forbidden path {forbidden}: {path}"
                )
        decoded = [payload.decode("utf-8", errors="ignore")]
        for offset in (0, 1):
            even_length = (len(payload) - offset) & ~1
            decoded.append(
                payload[offset:offset + even_length].decode(
                    "utf-16le", errors="ignore"
                )
            )
        for text in decoded:
            folded = text.casefold()
            for needle, forbidden in text_needles:
                if needle in folded:
                    raise ValueError(
                        f"SDK payload embeds forbidden path {forbidden}: {path}"
                    )


def _validate_payload_contract(
    root: Path,
    entries: list[dict[str, object]],
    profile: str,
    target: dict[str, object],
) -> None:
    paths = [str(entry["path"]) for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("SDK inventory contains duplicate paths")
    required = {
        *(f"include/lib{component}/{component}.h" for component in COMPONENTS),
        "lib/cmake/LarixFFmpegSDK/LarixFFmpegSDKConfig.cmake",
        "share/larix-ffmpeg-sdk/source.json",
        "share/larix-ffmpeg-sdk/build.json",
        "share/larix-ffmpeg-sdk/BUILD.txt",
    }
    target_id = str(target.get("id"))
    if target_id == "windows-x64-msvc":
        required.update(f"lib/{component}.lib" for component in COMPONENTS)
    required.update(runtime_files_for_target(target_id))
    missing = sorted(required - set(paths))
    if missing:
        raise ValueError(f"SDK package is missing required files: {missing}")
    runtime = sorted(path for path in paths if (
        PurePosixPath(path).parent.as_posix() == "bin"
        or path.startswith("lib/") and path.endswith(".dylib")
    ))
    if runtime != list(runtime_files_for_target(target_id)):
        raise ValueError(f"SDK runtime inventory is invalid: {runtime}")
    symbols = sorted(path for path in paths if path.startswith("symbols/"))
    if symbols != sorted(symbol_files_for_target(target_id)):
        raise ValueError("SDK symbol inventory is incomplete or unexpected")
    license_names = {
        PurePosixPath(path).name for path in paths if path.startswith("LICENSES/")
    }
    required_licenses = _required_license_files(profile)
    optional_licenses = {
        "COPYING.LGPLv3" if profile == "lgpl" else "COPYING.GPLv3"
    }
    if (
        not required_licenses <= license_names
        or not license_names <= required_licenses | optional_licenses
    ):
        raise ValueError("SDK license payload does not match its effective license")
    lowered = {path.casefold() for path in paths}
    for path in lowered:
        name = PurePosixPath(path).name
        if name in _FORBIDDEN_NAMES or any(
            marker in name for marker in _FORBIDDEN_COMPONENT_MARKERS
        ):
            raise ValueError(f"SDK package contains a forbidden component: {path}")
        if name.endswith(".a"):
            raise ValueError(f"SDK package contains a static archive: {path}")
        if target_id == "macos-arm64" and name.endswith((".dll", ".lib", ".exe")):
            raise ValueError(f"macOS SDK contains a Windows artifact: {path}")
        if target_id == "windows-x64-msvc" and name.endswith(".dylib"):
            raise ValueError(f"Windows SDK contains a macOS artifact: {path}")


def _build_manifest(
    repo_root: Path,
    lock: dict[str, object],
    profile: str,
    target: dict[str, object],
    entries: list[dict[str, object]],
    toolchain: dict[str, str],
    runtime_dependencies: dict[str, list[str]],
) -> dict[str, object]:
    source = lock["source"]
    configure_args = list(compose_configure_args(repo_root, profile, str(target["id"])))
    target_id = str(target["id"])
    configure_args.extend(platform_configure_args(target_id))
    return {
        "assetName": target_asset_name(lock, profile, target),
        "components": list(COMPONENTS),
        "configureArgs": configure_args,
        "effectiveLicense": _effective_license(profile),
        "files": entries,
        "libraryVersions": dict(lock["libraryVersions"]),
        "licenseProfile": profile,
        "packageFormat": target["packageFormat"],
        "packagingRevision": lock["packagingRevision"],
        "patches": _patch_manifest(repo_root),
        "releaseTag": lock["releaseTag"],
        "runtimeDependencies": runtime_dependencies,
        "runtimeFiles": list(runtime_files_for_target(target_id)),
        "schemaVersion": 1,
        "source": dict(source),
        "symbols": list(symbol_files_for_target(target_id)),
        "target": dict(target),
        "toolchain": toolchain,
        "upstreamVersion": lock["upstreamVersion"],
    }


def _sbom(manifest: dict[str, object]) -> dict[str, object]:
    files = []
    for index, entry in enumerate(manifest["files"]):
        files.append(
            {
                "SPDXID": f"SPDXRef-File-{index + 1}",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": entry["sha256"]}
                ],
                "copyrightText": "NOASSERTION",
                "fileName": f"./{entry['path']}",
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
            }
        )
    target = manifest["target"]
    sdk_package_id = "SPDXRef-Package-LarixFFmpegSDK"
    source_package_id = "SPDXRef-Package-FFmpeg"
    aggregate_license = f"MIT AND {manifest['effectiveLicense']}"
    sdk_package = {
        "SPDXID": sdk_package_id,
        "name": "Larix FFmpeg SDK",
        "versionInfo": manifest["releaseTag"],
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseDeclared": aggregate_license,
        "licenseConcluded": aggregate_license,
        "copyrightText": "NOASSERTION",
    }
    source_package = {
        "SPDXID": source_package_id,
        "name": "FFmpeg",
        "versionInfo": manifest["upstreamVersion"],
        "downloadLocation": manifest["source"]["url"],
        "filesAnalyzed": False,
        "checksums": [
            {
                "algorithm": "SHA256",
                "checksumValue": manifest["source"]["sha256"],
            }
        ],
        "licenseDeclared": "NOASSERTION",
        "licenseConcluded": "NOASSERTION",
        "copyrightText": "NOASSERTION",
    }
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": sdk_package_id,
        },
        {
            "spdxElementId": sdk_package_id,
            "relationshipType": "GENERATED_FROM",
            "relatedSpdxElement": source_package_id,
        }
    ]
    relationships.extend(
        {
            "spdxElementId": sdk_package_id,
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": entry["SPDXID"],
        }
        for entry in files
    )
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "creators": ["Tool: LarixFFmpegSDK-release-manifest"],
            "created": "1980-01-01T00:00:00Z",
        },
        "dataLicense": "CC0-1.0",
        "documentNamespace": (
            "https://github.com/AlexBianzd/LarixFFmpegSDK/releases/tag/"
            f"{manifest['releaseTag']}#{manifest['licenseProfile']}-{target['id']}"
        ),
        "files": files,
        "name": f"Larix FFmpeg SDK {manifest['licenseProfile']} {target['id']}",
        "packages": [sdk_package, source_package],
        "relationships": relationships,
        "spdxVersion": "SPDX-2.3",
    }


def _write_checksums(root: Path, destination: Path) -> None:
    lines = []
    for path in _walk_regular_files(root, include_generated=True):
        relative = path.relative_to(root).as_posix()
        if relative == (_METADATA_DIRECTORY / "SHA256SUMS").as_posix() or path == destination:
            continue
        _, digest = _hash_file(path)
        lines.append(f"{digest}  {relative}\n")
    destination.write_bytes("".join(lines).encode("utf-8"))


def _resolve_contract(
    repo_or_lock: Path | dict[str, object], target: dict[str, object]
) -> tuple[Path | None, dict[str, object], dict[str, object]]:
    if isinstance(repo_or_lock, Path):
        repo_root = repo_or_lock.resolve()
        lock = load_lock(repo_root / "config" / "ffmpeg.lock.json")
        exact_target = load_target(
            repo_root / "config" / "targets" / f"{target['id']}.json"
        )
        if target != exact_target:
            raise ValueError("target does not match repository contract")
        return repo_root, lock, exact_target
    return None, repo_or_lock, target


def generate_release_metadata(
    sdk_root: Path,
    repo_or_lock: Path | dict[str, object],
    profile: str,
    target: dict[str, object],
    *,
    toolchain: dict[str, str] | None = None,
    runtime_dependencies: dict[str, list[str]] | None = None,
    forbidden_paths: tuple[str, ...] = (),
) -> dict[str, object]:
    repo_root, lock, target = _resolve_contract(repo_or_lock, target)
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    if toolchain is None:
        toolchain = (
            {"compiler": "MSVC unknown", "windowsSdk": "unknown"}
            if target["platform"] == "windows"
            else {
                "compiler": "Apple clang unknown",
                "xcode": "Xcode unknown",
                "macosSdk": "unknown",
            }
        )
    toolchain = _normalize_toolchain(toolchain, target)
    runtime_files = runtime_files_for_target(str(target["id"]))
    runtime_dependencies = _normalize_dependencies(
        runtime_dependencies
        or {
            path: [
                "KERNEL32.dll"
                if target["platform"] == "windows"
                else "/usr/lib/libSystem.B.dylib"
            ]
            for path in runtime_files
        },
        target,
    )
    metadata = sdk_root / _METADATA_DIRECTORY.as_posix()
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "source.json").write_bytes(_canonical_json(lock["source"]))
    build = {
        "configureArgs": list(compose_configure_args(repo_root, profile, str(target["id"])))
        + list(platform_configure_args(str(target["id"]))),
        "pathPolicy": "physical-roots-scanned-not-recorded",
        "runtimeDependencies": runtime_dependencies,
        "toolchain": toolchain,
    }
    (metadata / "build.json").write_bytes(_canonical_json(build))
    _scan_forbidden_paths(sdk_root, forbidden_paths)
    entries = _inventory(sdk_root)
    _validate_payload_contract(sdk_root, entries, profile, target)
    manifest = _build_manifest(
        repo_root, lock, profile, target, entries, toolchain, runtime_dependencies
    )
    validate_release_manifest_schema(manifest, repo_root)
    (metadata / "manifest.json").write_bytes(_canonical_json(manifest))
    (metadata / "sbom.spdx.json").write_bytes(_canonical_json(_sbom(manifest)))
    _write_checksums(sdk_root, metadata / "SHA256SUMS")
    verify_release_metadata(sdk_root, repo_root)
    return manifest


def _verify_entries(entries: object) -> list[dict[str, object]]:
    if not isinstance(entries, list):
        raise ValueError("release manifest file inventory is invalid")
    previous = ""
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise ValueError("release manifest file entry is invalid")
        path = _validate_relative_path(entry.get("path"))
        if path in seen or (previous and path <= previous):
            raise ValueError("release manifest paths are duplicate or unsorted")
        seen.add(path)
        previous = path
        if (
            type(entry.get("size")) is not int
            or entry["size"] < 0
            or not isinstance(entry.get("sha256"), str)
            or len(entry["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in entry["sha256"])
        ):
            raise ValueError("release manifest file digest is invalid")
    return entries


def _verify_sbom(manifest: dict[str, object], sbom: dict[str, object]) -> None:
    expected = _sbom(manifest)
    if sbom != expected:
        raise ValueError("SPDX SBOM does not correspond exactly to the manifest")


def verify_release_metadata(
    sdk_root: Path, repo_root: Path | None = None
) -> dict[str, object]:
    metadata = sdk_root / _METADATA_DIRECTORY.as_posix()
    manifest = _load_canonical_json(metadata / "manifest.json")
    contract_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    validate_release_manifest_schema(manifest, contract_root)
    profile = manifest.get("licenseProfile")
    target = manifest.get("target")
    if not isinstance(profile, str) or not isinstance(target, dict):
        raise ValueError("release manifest identity is invalid")
    target_id = str(target.get("id"))
    _verify_staged_provenance(sdk_root, contract_root, target_id)
    lock = load_lock(contract_root / "config" / "ffmpeg.lock.json")
    expected_target = load_target(
        contract_root / "config" / "targets" / f"{target.get('id')}.json"
    )
    expected_identity = {
        "assetName": target_asset_name(lock, profile, expected_target),
        "components": list(COMPONENTS),
        "configureArgs": list(
            compose_configure_args(contract_root, profile, str(expected_target["id"]))
        )
        + list(platform_configure_args(str(expected_target["id"]))),
        "effectiveLicense": _effective_license(profile),
        "libraryVersions": dict(lock["libraryVersions"]),
        "packageFormat": expected_target["packageFormat"],
        "packagingRevision": lock["packagingRevision"],
        "patches": _patch_manifest(contract_root),
        "releaseTag": lock["releaseTag"],
        "runtimeFiles": list(runtime_files_for_target(str(expected_target["id"]))),
        "source": lock["source"],
        "symbols": list(symbol_files_for_target(str(expected_target["id"]))),
        "target": expected_target,
        "upstreamVersion": lock["upstreamVersion"],
    }
    for key, expected in expected_identity.items():
        if manifest.get(key) != expected:
            raise ValueError(f"release manifest {key} does not match repository contract")
    toolchain = _normalize_toolchain(manifest.get("toolchain", {}), expected_target)
    dependencies = _normalize_dependencies(
        manifest.get("runtimeDependencies", {}), expected_target
    )
    build = _load_canonical_json(metadata / "build.json")
    if set(build) != {"configureArgs", "pathPolicy", "runtimeDependencies", "toolchain"}:
        raise ValueError("embedded build provenance fields are invalid")
    if (
        build["configureArgs"] != manifest["configureArgs"]
        or build["runtimeDependencies"] != dependencies
        or build["toolchain"] != toolchain
        or build["pathPolicy"] != "physical-roots-scanned-not-recorded"
    ):
        raise ValueError("manifest does not match embedded build provenance")
    source = _load_canonical_json(metadata / "source.json")
    if source != lock["source"]:
        raise ValueError("embedded source provenance does not match the lock")
    entries = _verify_entries(manifest.get("files"))
    actual = _inventory(sdk_root)
    if entries != actual:
        raise ValueError("SDK contents do not match the release manifest")
    _validate_payload_contract(sdk_root, actual, profile, expected_target)
    _verify_sbom(manifest, _load_canonical_json(metadata / "sbom.spdx.json"))
    expected_sums = metadata / ".expected-SHA256SUMS"
    try:
        _write_checksums(sdk_root, expected_sums)
        if (metadata / "SHA256SUMS").read_bytes() != expected_sums.read_bytes():
            raise ValueError("SDK checksums do not match the package")
    finally:
        expected_sums.unlink(missing_ok=True)
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--profile", choices=("lgpl", "gpl"), required=True)
    parser.add_argument("--target", default="windows-x64-msvc")
    parser.add_argument("--build-info", type=Path, required=True)
    arguments = parser.parse_args()
    build = _load_build_info(arguments.build_info)
    target = load_target(
        arguments.repo_root / "config" / "targets" / f"{arguments.target}.json"
    )
    generate_release_metadata(
        arguments.sdk_root,
        arguments.repo_root,
        arguments.profile,
        target,
        toolchain=build["toolchain"],
        runtime_dependencies=build["runtimeDependencies"],
        forbidden_paths=tuple(build["forbiddenPaths"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
