from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.model import (
    compose_configure_args,
    load_lock,
    load_target,
    target_asset_name,
)
from scripts.common.release_manifest import _FORBIDDEN_COMPONENT_MARKERS


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPOSITORY_ROOT / "config" / "ffmpeg.lock.json"
TARGET_DIRECTORY = REPOSITORY_ROOT / "config" / "targets"

COMMON_ARGS = (
    "--disable-autodetect",
    "--disable-static",
    "--enable-shared",
    "--disable-doc",
    "--disable-ffmpeg",
    "--disable-ffplay",
    "--enable-ffprobe",
    "--disable-avfilter",
    "--disable-avdevice",
    "--disable-stripping",
)

WINDOWS_TARGET = {
    "id": "windows-x64-msvc",
    "platform": "windows",
    "architecture": "x86_64",
    "abi": "msvc",
    "toolchain": "vs2022-msvc",
    "linkage": "shared",
    "packageFormat": "zip",
    "driver": "windows",
}

MACOS_TARGET = {
    "id": "macos-arm64",
    "platform": "macos",
    "architecture": "arm64",
    "abi": "darwin",
    "toolchain": "xcode-clang",
    "minimumOsVersion": "12.0",
    "linkage": "shared",
    "packageFormat": "tar.xz",
    "driver": "macos",
}


class TargetLoadingTests(unittest.TestCase):
    def test_target_schema_freezes_desktop_and_macos_boundaries(self) -> None:
        schema = json.loads(
            (REPOSITORY_ROOT / "config" / "schema" / "target.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["additionalProperties"], False)
        self.assertEqual(len(schema["allOf"]), 2)
        self.assertEqual(
            schema["allOf"][0]["then"]["properties"]["linkage"],
            {"const": "shared"},
        )
        macos_then = schema["allOf"][1]["then"]
        self.assertEqual(macos_then["required"], ["minimumOsVersion"])
        self.assertEqual(
            macos_then["properties"],
            {
                "architecture": {"const": "arm64"},
                "minimumOsVersion": {"const": "12.0"},
            },
        )

    def test_loads_the_exact_initial_target_contracts(self) -> None:
        cases = (
            ("windows-x64-msvc", WINDOWS_TARGET),
            ("macos-arm64", MACOS_TARGET),
        )

        for target_id, expected in cases:
            with self.subTest(target_id=target_id):
                self.assertEqual(load_target(TARGET_DIRECTORY / f"{target_id}.json"), expected)

    def test_accepts_a_valid_future_static_target(self) -> None:
        future_target = {
            "id": "wasm32-emscripten",
            "platform": "wasm",
            "architecture": "wasm32",
            "abi": "emscripten",
            "toolchain": "emsdk",
            "linkage": "static",
            "packageFormat": "tar.xz",
            "driver": "wasm",
        }
        self.assert_target_accepted(future_target)

    def test_rejects_invalid_target_contracts(self) -> None:
        cases = (
            ("unknown field", {"unexpected": "value"}),
            ("desktop static linkage", {"linkage": "static"}),
            ("macos unsupported architecture", {"architecture": "x86_64"}),
            ("macos unsupported deployment", {"minimumOsVersion": "13.0"}),
            ("boolean identifier", {"id": True}),
            ("invalid linkage", {"linkage": "dynamic"}),
            ("invalid package format", {"packageFormat": "7z"}),
            ("known target drift", {"architecture": "arm64"}),
        )

        for name, replacement in cases:
            with self.subTest(name=name):
                candidate = copy.deepcopy(
                    MACOS_TARGET if name.startswith("macos") else WINDOWS_TARGET
                )
                candidate.update(replacement)
                self.assert_target_rejected(candidate)

    def assert_target_accepted(self, candidate: dict[str, object]) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "target.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            self.assertEqual(load_target(path), candidate)

    def assert_target_rejected(self, candidate: dict[str, object]) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "target.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_target(path)


class ConfigureCompositionTests(unittest.TestCase):
    def test_ffmpeg_9_excludes_obsolete_postproc_option_but_package_policy_remains(self) -> None:
        arguments = compose_configure_args(
            REPOSITORY_ROOT, "lgpl", "windows-x64-msvc"
        )
        self.assertNotIn("--disable-postproc", arguments)
        self.assertIn("postproc", _FORBIDDEN_COMPONENT_MARKERS)

    def test_composes_profiles_in_a_deterministic_order(self) -> None:
        lgpl = compose_configure_args(REPOSITORY_ROOT, "lgpl", "windows-x64-msvc")
        gpl = compose_configure_args(REPOSITORY_ROOT, "gpl", "windows-x64-msvc")

        self.assertEqual(lgpl, COMMON_ARGS)
        self.assertEqual(gpl, COMMON_ARGS + ("--enable-gpl",))

    def test_profiles_are_independent_and_share_the_common_boundary(self) -> None:
        for profile in ("lgpl", "gpl"):
            with self.subTest(profile=profile):
                arguments = compose_configure_args(
                    REPOSITORY_ROOT, profile, "macos-arm64"
                )
                self.assertEqual(arguments[: len(COMMON_ARGS)], COMMON_ARGS)
                self.assertNotIn("--enable-nonfree", arguments)

        lgpl = compose_configure_args(REPOSITORY_ROOT, "lgpl", "macos-arm64")
        gpl = compose_configure_args(REPOSITORY_ROOT, "gpl", "macos-arm64")
        self.assertNotIn("--enable-gpl", lgpl)
        self.assertEqual(gpl.count("--enable-gpl"), 1)

    def test_rejects_duplicate_configure_arguments(self) -> None:
        self.assert_profile_rejected("lgpl", "--disable-autodetect\n")
        self.assert_profile_rejected(
            "gpl", "--enable-gpl\n--extra-cflags=-O2\n--extra-cflags=-O3\n"
        )

    def test_rejects_blank_or_comment_profile_arguments(self) -> None:
        self.assert_profile_rejected("gpl", "\n--enable-gpl\n")
        self.assert_profile_rejected("gpl", "# hidden argument\n--enable-gpl\n")

    def test_rejects_nonfree_and_gpl_incompatible_profiles(self) -> None:
        self.assert_profile_rejected("lgpl", "--enable-gpl\n")
        self.assert_profile_rejected("lgpl", "--enable-gpl=yes\n")
        self.assert_profile_rejected("gpl", "--enable-nonfree\n")
        self.assert_profile_rejected("gpl", "--enable-gpl\n--foo=nonfree\n")

    def test_rejects_unrecognized_profile_or_target(self) -> None:
        with self.assertRaises(ValueError):
            compose_configure_args(REPOSITORY_ROOT, "nonfree", "windows-x64-msvc")
        with self.assertRaises(ValueError):
            compose_configure_args(REPOSITORY_ROOT, "lgpl", "unknown-target")

    def test_derives_release_asset_names_from_the_target_package_format(self) -> None:
        lock = load_lock(LOCK_PATH)
        self.assertEqual(
            target_asset_name(lock, "lgpl", WINDOWS_TARGET),
            "larix-ffmpeg-sdk-9.0.1-larix.1-lgpl-windows-x64-msvc.zip",
        )
        self.assertEqual(
            target_asset_name(lock, "gpl", MACOS_TARGET),
            "larix-ffmpeg-sdk-9.0.1-larix.1-gpl-macos-arm64.tar.xz",
        )

    def assert_profile_rejected(self, profile: str, contents: str) -> None:
        profile_path = REPOSITORY_ROOT / "config" / "profiles" / f"{profile}.conf"
        original = profile_path.read_text(encoding="utf-8")
        try:
            profile_path.write_text(contents, encoding="utf-8")
            with self.assertRaises(ValueError):
                compose_configure_args(REPOSITORY_ROOT, profile, "windows-x64-msvc")
        finally:
            profile_path.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
