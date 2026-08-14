from __future__ import annotations

from pathlib import Path
import io
import json
import tarfile
import tempfile
import unittest

import scripts.common.package as sdk_package
from scripts.common.model import load_target
from scripts.common.release_manifest import (
    COMPONENTS,
    generate_release_metadata,
    verify_release_metadata,
)
from scripts.common.verify_sdk import _require_inspection_report, _runtime_environment
from tests.test_manifest import LOCK, TARGET, create_sdk


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MACOS_TARGET = load_target(REPOSITORY_ROOT / "config" / "targets" / "macos-arm64.json")
MACOS_RUNTIME_FILES = tuple(sorted(
    ["bin/ffprobe"]
    + [
        f"lib/lib{component}.{LOCK['libraryVersions'][component]}.dylib"
        for component in COMPONENTS
    ]
))


def create_macos_sdk(root: Path, profile: str = "lgpl") -> None:
    license_file = "COPYING.LGPLv2.1" if profile == "lgpl" else "COPYING.GPLv2"
    files = {
        **{path: (path + "\n").encode("ascii") for path in MACOS_RUNTIME_FILES},
        **{
            f"include/lib{component}/{component}.h": f"{component}\n".encode("ascii")
            for component in COMPONENTS
        },
        "lib/cmake/LarixFFmpegSDK/LarixFFmpegSDKConfig.cmake": b"config\n",
        "LICENSES/FFmpeg-LICENSE.md": b"FFmpeg license\n",
        f"LICENSES/{license_file}": b"license\n",
        "LICENSES/LarixFFmpegSDK-MIT.txt": b"MIT\n",
        "share/larix-ffmpeg-sdk/source.json": (
            json.dumps(LOCK["source"], indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        "share/larix-ffmpeg-sdk/BUILD.txt": b"Reproducible build instructions\n",
        "share/larix-ffmpeg-sdk/provenance/config/ffmpeg.lock.json": (
            REPOSITORY_ROOT / "config" / "ffmpeg.lock.json"
        ).read_bytes(),
        "share/larix-ffmpeg-sdk/provenance/config/macos-arm64.json": (
            REPOSITORY_ROOT / "config" / "targets" / "macos-arm64.json"
        ).read_bytes(),
        "share/larix-ffmpeg-sdk/provenance/config/common.conf": (
            REPOSITORY_ROOT / "config" / "profiles" / "common.conf"
        ).read_bytes(),
        "share/larix-ffmpeg-sdk/provenance/config/lgpl.conf": (
            REPOSITORY_ROOT / "config" / "profiles" / "lgpl.conf"
        ).read_bytes(),
        "share/larix-ffmpeg-sdk/provenance/config/gpl.conf": (
            REPOSITORY_ROOT / "config" / "profiles" / "gpl.conf"
        ).read_bytes(),
        "share/larix-ffmpeg-sdk/provenance/patches/README.md": (
            REPOSITORY_ROOT / "patches" / "9.0.1" / "README.md"
        ).read_bytes(),
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


class MacOSDriverContractTests(unittest.TestCase):
    def test_repository_owns_the_public_and_platform_macos_drivers(self) -> None:
        required = (
            REPOSITORY_ROOT / "scripts" / "build-macos.sh",
            REPOSITORY_ROOT / "scripts" / "platforms" / "macos" / "build.sh",
            REPOSITORY_ROOT / "scripts" / "platforms" / "macos" / "inspect.sh",
        )
        for path in required:
            with self.subTest(path=path.relative_to(REPOSITORY_ROOT)):
                self.assertTrue(path.is_file(), f"missing macOS driver: {path}")

    def test_drivers_freeze_arm64_macos_12_and_delegate_common_contracts(self) -> None:
        public = (REPOSITORY_ROOT / "scripts" / "build-macos.sh").read_text(
            encoding="utf-8"
        )
        build = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "macos" / "build.sh"
        ).read_text(encoding="utf-8")
        inspect = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "macos" / "inspect.sh"
        ).read_text(encoding="utf-8")

        for source in (public, build, inspect):
            self.assertTrue(source.startswith("#!/usr/bin/env bash\n"))
            self.assertIn("set -euo pipefail", source)
        self.assertIn("--profile", public)
        self.assertIn("--configuration", public)
        self.assertIn("--output-root", public)
        self.assertIn("platforms/macos/build.sh", public)

        self.assertIn("MACOSX_DEPLOYMENT_TARGET=12.0", build)
        self.assertIn("--arch=arm64", build)
        self.assertIn("--target-os=darwin", build)
        self.assertIn("--install-name-dir=@rpath", build)
        self.assertIn("--prefix=../install", build)
        self.assertNotIn('"--prefix=$install_root"', build)
        self.assertIn("-ffile-prefix-map=./src=larix-source", build)
        self.assertIn("-fdebug-compilation-dir=larix-build", build)
        self.assertIn('ln -s "$source_root" "$build_root/src"', build)
        self.assertIn('"$build_root/src/configure"', build)
        self.assertIn("absolute_prefix_maps=", build)
        self.assertIn("export CFLAGS=", build)
        configure_scope = build[
            build.index("configure_args+=("):
            build.index('cd "$build_root"')
        ]
        self.assertNotIn("-ffile-prefix-map=$source_root", configure_scope)
        self.assertIn("compose_configure_args", build)
        self.assertIn("macos-arm64", build)
        self.assertIn("scripts.common.source", build)
        self.assertIn("scripts.common.release_manifest", build)
        self.assertIn("scripts.common.package", build)
        self.assertIn("scripts.common.verify_sdk", build)
        self.assertIn('export PYTHONPATH="$repo_root"', build)
        self.assertIn('strip -S "$stage_root/$relative"', build)
        self.assertIn('runtime_files_for_target("macos-arm64")', build)
        self.assertLess(
            build.index('strip -S "$stage_root/$relative"'),
            build.index("scripts.common.release_manifest"),
        )
        self.assertRegex(build, r"for tool in [^\n]*\bstrip\b")

        for tool in ("file", "otool", "vtool"):
            self.assertIn(tool, inspect)
        self.assertIn("arm64", inspect)
        self.assertIn("12.0", inspect)
        self.assertIn("@rpath", inspect)
        self.assertNotIn("| head -n 1", inspect)
        self.assertIn("find \"$sdk_root\" -type f -print0", inspect)
        self.assertIn("unexpected Mach-O", inspect)
        combined = public + build + inspect
        for unsupported in ("x86_64", "universal2", "lipo"):
            self.assertNotIn(unsupported, combined)


class TarXzPackageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sdk = self.root / "sdk"
        create_sdk(self.sdk)
        generate_release_metadata(self.sdk, LOCK, "lgpl", TARGET)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_tar_xz_is_deterministic_normalized_and_relocatable(self) -> None:
        self.assertTrue(hasattr(sdk_package, "create_tar_xz_package"))
        self.assertTrue(hasattr(sdk_package, "extract_tar_xz_package"))
        first = self.root / "first.tar.xz"
        second = self.root / "second.tar.xz"
        sdk_package.create_tar_xz_package(self.sdk, first)
        sdk_package.create_tar_xz_package(self.sdk, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first, mode="r:xz") as archive:
            members = archive.getmembers()
            self.assertEqual([member.name for member in members], sorted(
                member.name for member in members
            ))
            self.assertTrue(all(member.isreg() for member in members))
            self.assertTrue(all(member.mtime == 0 for member in members))
            self.assertTrue(all(member.uid == 0 and member.gid == 0 for member in members))
            self.assertTrue(all(not member.uname and not member.gname for member in members))
        relocated = self.root / "relocated"
        sdk_package.extract_tar_xz_package(first, relocated)
        self.assertEqual(
            (relocated / "include" / "libavcodec" / "avcodec.h").read_bytes(),
            b"avcodec\n",
        )

    def test_tar_xz_extraction_rejects_escaping_links_and_special_entries(self) -> None:
        self.assertTrue(hasattr(sdk_package, "extract_tar_xz_package"))
        cases = {
            "traversal": ("../escape", tarfile.REGTYPE),
            "absolute": ("/escape", tarfile.REGTYPE),
            "symlink": ("link", tarfile.SYMTYPE),
            "hardlink": ("link", tarfile.LNKTYPE),
            "fifo": ("pipe", tarfile.FIFOTYPE),
        }
        for name, (entry_name, entry_type) in cases.items():
            with self.subTest(name=name):
                archive_path = self.root / f"{name}.tar.xz"
                with tarfile.open(archive_path, mode="w:xz") as archive:
                    info = tarfile.TarInfo(entry_name)
                    info.type = entry_type
                    info.size = 1 if entry_type == tarfile.REGTYPE else 0
                    info.linkname = "target" if entry_type in {
                        tarfile.SYMTYPE, tarfile.LNKTYPE
                    } else ""
                    archive.addfile(info, io.BytesIO(b"x") if info.size else None)
                destination = self.root / f"out-{name}"
                with self.assertRaises(ValueError):
                    sdk_package.extract_tar_xz_package(archive_path, destination)
                self.assertFalse(destination.exists())


class MacOSReleaseManifestContractTests(unittest.TestCase):
    def test_manifest_binds_macos_runtime_toolchain_and_package_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sdk = Path(temporary) / "sdk"
            create_macos_sdk(sdk)
            dependencies = {
                path: (["/usr/lib/libSystem.B.dylib"] if path == "bin/ffprobe" else [
                    "@rpath/" + Path(path).name,
                    "/usr/lib/libSystem.B.dylib",
                ])
                for path in MACOS_RUNTIME_FILES
            }
            toolchain = {
                "compiler": "Apple clang version 17.0.0",
                "xcode": "Xcode 16.4",
                "macosSdk": "15.5",
            }
            manifest = generate_release_metadata(
                sdk,
                REPOSITORY_ROOT,
                "lgpl",
                MACOS_TARGET,
                toolchain=toolchain,
                runtime_dependencies=dependencies,
                forbidden_paths=(str(sdk.parent),),
            )
            self.assertEqual(manifest["packageFormat"], "tar.xz")
            self.assertEqual(manifest["runtimeFiles"], list(MACOS_RUNTIME_FILES))
            self.assertEqual(manifest["symbols"], [])
            self.assertEqual(manifest["toolchain"], toolchain)
            self.assertIn("--arch=arm64", manifest["configureArgs"])
            self.assertIn("--target-os=darwin", manifest["configureArgs"])
            self.assertIn("--install-name-dir=@rpath", manifest["configureArgs"])
            _require_inspection_report(
                {
                    "runtimeDependencies": dependencies,
                    "toolchain": toolchain,
                },
                manifest,
            )
            self.assertEqual(verify_release_metadata(sdk, REPOSITORY_ROOT), manifest)


class MacOSConsumerContractTests(unittest.TestCase):
    def test_runtime_environment_uses_only_the_relocated_macos_library_directory(self) -> None:
        sdk = Path("relocated-sdk")
        environment = _runtime_environment(
            "macos-arm64",
            sdk,
            {"PATH": "/usr/bin", "DYLD_LIBRARY_PATH": "/foreign/lib"},
        )
        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["DYLD_LIBRARY_PATH"], str(sdk / "lib"))

    def test_cmake_config_exports_exact_arm64_dylib_locations(self) -> None:
        source = (
            REPOSITORY_ROOT / "cmake" / "LarixFFmpegSDKConfig.cmake.in"
        ).read_text(encoding="utf-8")
        self.assertIn("elseif(APPLE)", source)
        self.assertIn("CMAKE_SYSTEM_PROCESSOR", source)
        self.assertIn("CMAKE_OSX_ARCHITECTURES", source)
        for component, version in LOCK["libraryVersions"].items():
            self.assertIn(f"lib{component}.{version}.dylib", source)
        self.assertIn("IMPORTED_LOCATION", source)
        self.assertIn("INTERFACE_INCLUDE_DIRECTORIES", source)
        consumer = (REPOSITORY_ROOT / "tests" / "consumer" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("if(WIN32)", consumer)
        self.assertIn("get_target_property(_implib", consumer)
        self.assertIn('if(NOT EXISTS "${_runtime}")', consumer)

    def test_verifier_dispatches_tar_xz_to_native_macos_inspection(self) -> None:
        source = (REPOSITORY_ROOT / "scripts" / "common" / "verify_sdk.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("extract_package", source)
        self.assertIn('target_id == "macos-arm64"', source)
        self.assertIn("scripts/platforms/macos/inspect.sh", source)
        self.assertIn("DYLD_LIBRARY_PATH", source)
        self.assertIn('sdk / "bin" / "ffprobe"', source)


if __name__ == "__main__":
    unittest.main()
