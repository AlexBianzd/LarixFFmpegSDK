from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.common.release_manifest import (
    COMPONENTS,
    _load_build_info,
    generate_release_metadata,
    verify_release_metadata,
)


LOCK = {
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
    "libraryVersions": {
        "avutil": 61,
        "avcodec": 63,
        "avformat": 63,
        "swresample": 7,
        "swscale": 10,
    },
}
TARGET = {
    "id": "windows-x64-msvc",
    "platform": "windows",
    "architecture": "x86_64",
    "abi": "msvc",
    "toolchain": "vs2022-msvc",
    "linkage": "shared",
    "packageFormat": "zip",
    "driver": "windows",
}
RUNTIME_FILES = (
    "bin/avcodec-63.dll",
    "bin/avformat-63.dll",
    "bin/avutil-61.dll",
    "bin/ffprobe.exe",
    "bin/swresample-7.dll",
    "bin/swscale-10.dll",
)


def create_sdk(root: Path, profile: str = 'lgpl') -> None:
    files = {
        **{path: path.encode("ascii") for path in RUNTIME_FILES},
        **{
            f"include/lib{component}/{component}.h": f"{component}\n".encode("ascii")
            for component in COMPONENTS
        },
        **{
            f"lib/{component}.lib": f"import {component}\n".encode("ascii")
            for component in COMPONENTS
        },
        "lib/cmake/LarixFFmpegSDK/LarixFFmpegSDKConfig.cmake": b"config\n",
        "share/larix-ffmpeg-sdk/source.json": b"{}\n",
    }
    repository = Path(__file__).resolve().parents[1]
    license_file = 'COPYING.LGPLv2.1' if profile == 'lgpl' else 'COPYING.GPLv2'
    files.update({
        **{f'symbols/{Path(path).stem}.pdb': (path + '\n').encode('ascii') for path in RUNTIME_FILES},
        'LICENSES/FFmpeg-LICENSE.md': b'FFmpeg license\n',
        f'LICENSES/{license_file}': b'license\n',
        'LICENSES/LarixFFmpegSDK-MIT.txt': b'MIT\n',
        'share/larix-ffmpeg-sdk/source.json': (
            json.dumps(LOCK['source'], indent=2, sort_keys=True) + '\n').encode('utf-8'),
        'share/larix-ffmpeg-sdk/BUILD.txt': b'Reproducible build instructions\n',
        'share/larix-ffmpeg-sdk/provenance/config/ffmpeg.lock.json': (
            repository / 'config' / 'ffmpeg.lock.json').read_bytes(),
        'share/larix-ffmpeg-sdk/provenance/config/windows-x64-msvc.json': (
            repository / 'config' / 'targets' / 'windows-x64-msvc.json').read_bytes(),
        'share/larix-ffmpeg-sdk/provenance/config/common.conf': (
            repository / 'config' / 'profiles' / 'common.conf').read_bytes(),
        'share/larix-ffmpeg-sdk/provenance/config/lgpl.conf': (
            repository / 'config' / 'profiles' / 'lgpl.conf').read_bytes(),
        'share/larix-ffmpeg-sdk/provenance/config/gpl.conf': (
            repository / 'config' / 'profiles' / 'gpl.conf').read_bytes(),
        'share/larix-ffmpeg-sdk/provenance/patches/README.md': (
            repository / 'patches' / '9.0.1' / 'README.md').read_bytes(),
    })
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class ReleaseManifestTests(unittest.TestCase):
    def test_transient_build_info_accepts_powershell_json_but_rejects_duplicates(self) -> None:
        path = Path(self.temporary.name) / "build-info.json"
        value = {
            "forbiddenPaths": ["C:\\build"],
            "runtimeDependencies": {
                runtime: ["KERNEL32.DLL"] for runtime in RUNTIME_FILES
            },
            "toolchain": {
                "compiler": "MSVC 14.44.35213",
                "windowsSdk": "10.0.26100.0",
            },
        }
        path.write_text(json.dumps(value, indent=4), encoding="utf-8")
        self.assertEqual(_load_build_info(path), value)
        path.write_text(
            '{"forbiddenPaths":[],"forbiddenPaths":[],"runtimeDependencies":{},'
            '"toolchain":{}}',
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            _load_build_info(path)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "sdk"
        create_sdk(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def generate(self) -> dict[str, object]:
        return generate_release_metadata(self.root, LOCK, "lgpl", TARGET)

    def test_writes_canonical_complete_release_metadata(self) -> None:
        manifest = self.generate()
        metadata = self.root / "share" / "larix-ffmpeg-sdk"
        manifest_bytes = (metadata / "manifest.json").read_bytes()
        self.assertTrue(manifest_bytes.endswith(b"\n"))
        self.assertEqual(
            manifest_bytes,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        self.assertEqual(manifest["components"], list(COMPONENTS))
        self.assertEqual(manifest["runtimeFiles"], list(RUNTIME_FILES))
        paths = [entry["path"] for entry in manifest["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertNotIn("share/larix-ffmpeg-sdk/manifest.json", paths)
        for entry in manifest["files"]:
            payload = (self.root / entry["path"]).read_bytes()
            self.assertEqual(entry["size"], len(payload))
            self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())
        sbom = json.loads((metadata / "sbom.spdx.json").read_text(encoding="utf-8"))
        self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
        sums = (metadata / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        summed_paths = [line.split("  ", 1)[1] for line in sums]
        expected = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path.name != "SHA256SUMS"
        )
        self.assertEqual(summed_paths, expected)
        self.assertEqual(verify_release_metadata(self.root), manifest)

    def test_embedded_build_provenance_is_independent_of_physical_root(self) -> None:
        first = generate_release_metadata(
            self.root, LOCK, "lgpl", TARGET,
            forbidden_paths=("C:\\first\\physical-root",),
        )
        with tempfile.TemporaryDirectory() as temporary:
            other = Path(temporary) / "sdk"
            create_sdk(other)
            second = generate_release_metadata(
                other, LOCK, "lgpl", TARGET,
                forbidden_paths=("D:\\second\\physical-root",),
            )
            first_build = (
                self.root / "share" / "larix-ffmpeg-sdk" / "build.json"
            ).read_bytes()
            second_build = (
                other / "share" / "larix-ffmpeg-sdk" / "build.json"
            ).read_bytes()
        self.assertEqual(first, second)
        self.assertEqual(first_build, second_build)
        build = json.loads(first_build)
        self.assertEqual(build["pathPolicy"], "physical-roots-scanned-not-recorded")
        self.assertNotIn("forbiddenPaths", build)
        self.assertNotIn(b"C:\\\\first\\\\physical-root", first_build)
        self.assertNotIn(b"D:\\\\second\\\\physical-root", first_build)

    def test_rejects_missing_extra_forbidden_and_mutated_payloads(self) -> None:
        self.generate()
        cases = ("missing", "extra", "forbidden", "mutated")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as temporary:
                    copy = Path(temporary) / "sdk"
                    import shutil

                    shutil.copytree(self.root, copy)
                    if case == "missing":
                        (copy / "bin" / "avcodec-63.dll").unlink()
                    elif case == "extra":
                        (copy / "bin" / "unknown.dll").write_bytes(b"unknown")
                    elif case == "forbidden":
                        (copy / "bin" / "ffmpeg.exe").write_bytes(b"forbidden")
                    else:
                        (copy / "lib" / "avcodec.lib").write_bytes(b"tampered")
                    with self.assertRaises(ValueError):
                        verify_release_metadata(copy)

    def test_rejects_manifest_traversal_absolute_duplicate_and_wrong_inventory(self) -> None:
        self.generate()
        manifest_path = self.root / "share" / "larix-ffmpeg-sdk" / "manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        cases = (
            ("traversal", "../escape"),
            ("backslash", "bin\\escape.dll"),
            ("absolute", "C:/escape.dll"),
            ("duplicate", original["files"][1]["path"]),
        )
        for name, replacement in cases:
            with self.subTest(name=name):
                candidate = json.loads(json.dumps(original))
                candidate["files"][0]["path"] = replacement
                manifest_path.write_text(
                    json.dumps(candidate, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                with self.assertRaises(ValueError):
                    verify_release_metadata(self.root)
        candidate = json.loads(json.dumps(original))
        candidate["components"] = ["avutil"]
        manifest_path.write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaises(ValueError):
            verify_release_metadata(self.root)

    def test_rejects_drive_relative_and_nonportable_manifest_paths(self) -> None:
        self.generate()
        manifest_path = self.root / 'share' / 'larix-ffmpeg-sdk' / 'manifest.json'
        original = json.loads(manifest_path.read_text(encoding='utf-8'))
        for replacement in ('C:escape.dll', 'bin/trailing.', 'bin/AUX.txt'):
            with self.subTest(replacement=replacement):
                candidate = json.loads(json.dumps(original))
                candidate['files'][0]['path'] = replacement
                manifest_path.write_text(
                    json.dumps(candidate, indent=2, sort_keys=True) + '\n',
                    encoding='utf-8', newline='\n')
                with self.assertRaises(ValueError):
                    verify_release_metadata(self.root)

    def test_rejects_symlinks_in_the_package_tree(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        link = self.root / "include" / "linked.h"
        try:
            link.symlink_to(self.root / "include" / "libavutil" / "avutil.h")
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable: {error}")
        with self.assertRaises(ValueError):
            self.generate()


if __name__ == "__main__":
    unittest.main()
