import unittest
import os
import tempfile
from pathlib import Path
from pathlib import PureWindowsPath
import scripts.common.build_dependencies as build


class CMakePathTests(unittest.TestCase):
    def test_native_windows_paths_are_not_cmake_escape_sequences(self):
        normalize = getattr(build, "cmake_arguments", None)
        self.assertTrue(callable(normalize), "CMake argument normalization is missing")
        got = normalize(
            [
                PureWindowsPath("C:/Program Files/CMake/bin/cmake.exe"),
                "-DZIMG_SOURCE=D:\\a\\sources\\zimg",
                "-DCMAKE_CXX_FLAGS=/MT /pathmap:D:\\a\\sources=zimg-source",
            ]
        )
        self.assertEqual(
            got,
            [
                "C:/Program Files/CMake/bin/cmake.exe",
                "-DZIMG_SOURCE=D:/a/sources/zimg",
                "-DCMAKE_CXX_FLAGS=/MT /pathmap:D:/a/sources=zimg-source",
            ],
        )

    def test_normalized_cmake_paths_are_removed_from_release_evidence(self):
        normalize = getattr(build, "normalize_build_evidence", None)
        self.assertTrue(callable(normalize), "build evidence normalization is missing")
        got = normalize(
            [
                {
                    "argv": [
                        "C:/Tools/cmake.exe",
                        "-DZIMG_SOURCE=D:/a/sdk/build/deps/sources/zimg",
                    ],
                    "cwd": "D:\\a\\sdk\\build\\deps",
                }
            ],
            PureWindowsPath("D:/a/sdk"),
            PureWindowsPath("D:/a/sdk/build/deps"),
        )
        self.assertEqual(
            got,
            [
                {
                    "argv": ["cmake.exe", "-DZIMG_SOURCE=${DEPENDENCIES}/sources/zimg"],
                    "cwd": "${DEPENDENCIES}",
                }
            ],
        )

    def test_msys_drive_paths_are_not_embedded_in_release_evidence(self):
        got = build.normalize_build_evidence(
            [
                {
                    "argv": [
                        "C:/msys64/usr/bin/bash.exe",
                        "/d/a/sdk/build/deps/sources/x264/configure",
                    ],
                    "cwd": "D:/a/sdk/build/deps",
                }
            ],
            PureWindowsPath("D:/a/sdk"),
            PureWindowsPath("D:/a/sdk/build/deps"),
        )
        self.assertEqual(
            got,
            [
                {
                    "argv": ["bash.exe", "${DEPENDENCIES}/sources/x264/configure"],
                    "cwd": "${DEPENDENCIES}",
                }
            ],
        )

    @unittest.skipIf(
        os.name == "nt",
        "native SUBST behavior is covered by the full Windows package gate",
    )
    def test_build_directory_alias_is_not_resolved_to_physical_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            physical = root / "physical"
            physical.mkdir()
            alias = root / "stable"
            alias.symlink_to(physical, target_is_directory=True)
            self.assertEqual(
                build.build_path(alias / "dependencies"), alias / "dependencies"
            )
