"""Assemble and verify the immutable Larix FFmpeg SDK release catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.parse
import urllib.request

from scripts.common.model import load_lock, load_target, target_asset_name
from scripts.common.package import extract_package
from scripts.common.release_manifest import verify_release_metadata


RELEASE_CATALOG = "release-catalog.json"
RELEASE_SUMS = "SHA256SUMS"
_TARGET_IDS = ("macos-arm64", "windows-x64-msvc")
_MAX_RELEASE_ASSET_BYTES = 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _load_json(path: Path) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid release JSON: {path.name}") from error
    if not isinstance(value, dict) or path.read_bytes() != _canonical_json(value):
        raise ValueError(f"release JSON is not canonical: {path.name}")
    return value


def expected_release_asset_names(
    lock: dict[str, object], repo_root: Path
) -> set[str]:
    source = lock.get("source")
    profiles = lock.get("profiles")
    if not isinstance(source, dict) or not isinstance(source.get("archive"), str):
        raise ValueError("release source identity is invalid")
    if profiles != ["lgpl", "gpl"]:
        raise ValueError("release profile matrix is invalid")
    names = {source["archive"]}
    for target_id in _TARGET_IDS:
        target = load_target(repo_root / "config" / "targets" / f"{target_id}.json")
        for profile in profiles:
            names.add(target_asset_name(lock, profile, target))
    return names


def _verify_source(path: Path, lock: dict[str, object]) -> None:
    source = lock.get("source")
    if not isinstance(source, dict):
        raise ValueError("release source identity is invalid")
    if path.name != source.get("archive"):
        raise ValueError("release source archive name is invalid")
    if path.stat().st_size != source.get("size") or _sha256(path) != source.get("sha256"):
        raise ValueError("release source archive does not match the lock")


def _verify_sdk_archive(archive: Path, repo_root: Path | None = None) -> dict[str, object]:
    contract_root = (repo_root or Path.cwd()).resolve()
    with tempfile.TemporaryDirectory(prefix="larix-release-sdk-") as temporary:
        sdk = extract_package(archive, Path(temporary) / "sdk")
        manifest = verify_release_metadata(sdk, contract_root)
        metadata = sdk / "share" / "larix-ffmpeg-sdk"
        required = (
            metadata / "manifest.json",
            metadata / "sbom.spdx.json",
            metadata / "build.json",
            metadata / "SHA256SUMS",
            metadata / "provenance" / "source.json",
            metadata / "provenance" / "patches" / "README.md",
            metadata / "provenance" / "config" / "profiles" / "common.conf",
            sdk / "LICENSES" / "LarixFFmpegSDK-MIT.txt",
            sdk / "LICENSES" / "FFmpeg-LICENSE.md",
        )
        if any(not path.is_file() for path in required):
            raise ValueError("SDK release provenance payload is incomplete")
        return manifest


def _expected_identity(
    name: str, lock: dict[str, object], repo_root: Path
) -> tuple[str, str] | None:
    for target_id in _TARGET_IDS:
        target = load_target(repo_root / "config" / "targets" / f"{target_id}.json")
        for profile in ("lgpl", "gpl"):
            if target_asset_name(lock, profile, target) == name:
                return profile, target_id
    return None


def _verify_sdk_identity(
    archive: Path,
    manifest: dict[str, object],
    lock: dict[str, object],
    repo_root: Path,
) -> tuple[str, str]:
    expected = _expected_identity(archive.name, lock, repo_root)
    target = manifest.get("target")
    if expected is None or not isinstance(target, dict):
        raise ValueError("unexpected SDK release archive")
    profile, target_id = expected
    if (
        manifest.get("assetName") != archive.name
        or manifest.get("releaseTag") != lock.get("releaseTag")
        or manifest.get("licenseProfile") != profile
        or manifest.get("source") != lock.get("source")
        or target.get("id") != target_id
    ):
        raise ValueError("SDK release archive identity does not match the lock")
    return profile, target_id


def _catalog_asset(
    path: Path, kind: str, profile: str | None = None, target: str | None = None
) -> dict[str, object]:
    entry: dict[str, object] = {
        "kind": kind,
        "name": path.name,
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }
    if profile is not None:
        entry["profile"] = profile
    if target is not None:
        entry["target"] = target
    return entry


def assemble_release_candidate(
    asset_directory: Path, tag: str, repo_root: Path
) -> dict[str, object]:
    root = asset_directory.resolve()
    lock = load_lock(repo_root / "config" / "ffmpeg.lock.json")
    if tag != lock.get("releaseTag"):
        raise ValueError("release tag does not match the lock")
    expected = expected_release_asset_names(lock, repo_root)
    observed = {path.name for path in root.iterdir() if path.is_file()}
    if observed != expected:
        raise ValueError("release candidate payload inventory is not exact")
    source_name = str(lock["source"]["archive"])
    _verify_source(root / source_name, lock)
    entries = [_catalog_asset(root / source_name, "source")]
    for name in sorted(expected - {source_name}):
        archive = root / name
        manifest = _verify_sdk_archive(archive, repo_root)
        profile, target_id = _verify_sdk_identity(
            archive, manifest, lock, repo_root
        )
        entries.append(_catalog_asset(archive, "sdk", profile, target_id))
    catalog: dict[str, object] = {
        "assets": sorted(entries, key=lambda entry: str(entry["name"])),
        "releaseTag": tag,
        "schemaVersion": 1,
        "source": lock["source"],
    }
    (root / RELEASE_CATALOG).write_bytes(_canonical_json(catalog))
    checksummed = sorted(expected | {RELEASE_CATALOG})
    sums = "".join(f"{_sha256(root / name)}  {name}\n" for name in checksummed)
    (root / RELEASE_SUMS).write_text(sums, encoding="utf-8", newline="\n")
    return verify_release_directory(root, tag, repo_root)


def _verify_sums(root: Path, names: set[str]) -> None:
    expected = "".join(f"{_sha256(root / name)}  {name}\n" for name in sorted(names))
    try:
        observed = (root / RELEASE_SUMS).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError("release SHA256SUMS is invalid") from error
    if observed != expected:
        raise ValueError("release SHA256SUMS does not match the assets")


def verify_release_directory(
    asset_directory: Path, tag: str, repo_root: Path
) -> dict[str, object]:
    root = asset_directory.resolve()
    lock = load_lock(repo_root / "config" / "ffmpeg.lock.json")
    if tag != lock.get("releaseTag"):
        raise ValueError("release tag does not match the lock")
    payload = expected_release_asset_names(lock, repo_root)
    expected_files = payload | {RELEASE_CATALOG, RELEASE_SUMS}
    observed = {path.name for path in root.iterdir() if path.is_file()}
    if observed != expected_files:
        raise ValueError("release asset inventory is not exact")
    _verify_sums(root, payload | {RELEASE_CATALOG})
    catalog = _load_json(root / RELEASE_CATALOG)
    if set(catalog) != {"assets", "releaseTag", "schemaVersion", "source"}:
        raise ValueError("release catalog fields are invalid")
    if (
        catalog.get("schemaVersion") != 1
        or catalog.get("releaseTag") != tag
        or catalog.get("source") != lock.get("source")
        or not isinstance(catalog.get("assets"), list)
    ):
        raise ValueError("release catalog identity is invalid")
    entries = catalog["assets"]
    names = [entry.get("name") for entry in entries if isinstance(entry, dict)]
    if len(names) != len(entries) or len(names) != len(set(names)) or set(names) != payload:
        raise ValueError("release catalog asset inventory is invalid")
    source_name = str(lock["source"]["archive"])
    _verify_source(root / source_name, lock)
    expected_entries = [_catalog_asset(root / source_name, "source")]
    for name in sorted(payload - {source_name}):
        archive = root / name
        manifest = _verify_sdk_archive(archive, repo_root)
        profile, target_id = _verify_sdk_identity(
            archive, manifest, lock, repo_root
        )
        expected_entries.append(_catalog_asset(archive, "sdk", profile, target_id))
    expected_entries.sort(key=lambda entry: str(entry["name"]))
    if entries != expected_entries:
        raise ValueError("release catalog entries do not match the assets")
    return catalog


def download_locked_source(asset_directory: Path, repo_root: Path) -> Path:
    lock = load_lock(repo_root / "config" / "ffmpeg.lock.json")
    source = lock["source"]
    destination = asset_directory / str(source["archive"])
    if destination.exists() or destination.is_symlink():
        raise ValueError("release source destination already exists")
    request = urllib.request.Request(str(source["url"]), headers={"User-Agent": "LarixFFmpegSDK/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.scheme != "https":
            raise ValueError("release source redirect is not HTTPS")
        data = response.read(int(source["size"]) + 1)
    if len(data) != source["size"] or hashlib.sha256(data).hexdigest() != source["sha256"]:
        raise ValueError("downloaded release source does not match the lock")
    destination.write_bytes(data)
    return destination


def verify_github_release(reference: str, repo_root: Path) -> dict[str, object]:
    if reference.count("@") != 1:
        raise ValueError("GitHub release reference must be owner/repository@tag")
    repository, tag = reference.split("@", 1)
    if repository.count("/") != 1 or not tag:
        raise ValueError("GitHub release reference is invalid")
    api = f"https://api.github.com/repos/{repository}/releases/tags/{urllib.parse.quote(tag)}"
    request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json", "User-Agent": "LarixFFmpegSDK/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    if release.get("tag_name") != tag or release.get("draft") or release.get("prerelease"):
        raise ValueError("GitHub Release identity is invalid")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("GitHub Release assets are invalid")
    with tempfile.TemporaryDirectory(prefix="larix-public-release-") as temporary:
        root = Path(temporary)
        names: set[str] = set()
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError("GitHub Release asset is invalid")
            name = asset.get("name")
            url = asset.get("browser_download_url")
            size = asset.get("size")
            if not isinstance(name, str) or name in names or not isinstance(url, str):
                raise ValueError("GitHub Release asset identity is invalid")
            if type(size) is not int or size < 0 or size > _MAX_RELEASE_ASSET_BYTES:
                raise ValueError("GitHub Release asset size is invalid")
            names.add(name)
            download = urllib.request.Request(url, headers={"User-Agent": "LarixFFmpegSDK/1"})
            with urllib.request.urlopen(download, timeout=60) as response:
                data = response.read(size + 1)
            if len(data) != size:
                raise ValueError("GitHub Release asset length is invalid")
            (root / name).write_bytes(data)
        return verify_release_directory(root, tag, repo_root)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-directory", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assemble", action="store_true")
    parser.add_argument("--download-source", action="store_true")
    parser.add_argument("--github-release")
    parser.add_argument("--anonymous", action="store_true")
    arguments = parser.parse_args()
    repo_root = arguments.repo_root.resolve()
    if arguments.github_release:
        if not arguments.anonymous or arguments.asset_directory or arguments.tag:
            parser.error("GitHub Release verification requires only --github-release --anonymous")
        verify_github_release(arguments.github_release, repo_root)
        return 0
    if arguments.asset_directory is None or not arguments.tag or arguments.anonymous:
        parser.error("local verification requires --asset-directory and --tag")
    root = arguments.asset_directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if arguments.download_source:
        download_locked_source(root, repo_root)
    if arguments.assemble:
        assemble_release_candidate(root, arguments.tag, repo_root)
    else:
        verify_release_directory(root, arguments.tag, repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
