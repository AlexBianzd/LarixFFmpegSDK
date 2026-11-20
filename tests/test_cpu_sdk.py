from pathlib import Path
import unittest
from scripts.common.model import load_lock, compose_configure_args
from scripts.common.release_manifest import runtime_files_for_target

ROOT = Path(__file__).resolve().parents[1]


class CPUSDKContractTests(unittest.TestCase):
    def test_sdk_contains_cpu_filter_library(self):
        self.assertIn(
            "lib/libavfilter.12.dylib", runtime_files_for_target("macos-arm64")
        )
        self.assertIn(
            "bin/avfilter-12.dll", runtime_files_for_target("windows-x64-msvc")
        )

    def test_profiles_enable_cpu_processing_and_isolate_gpl_encoders(self):
        for target in ["macos-arm64", "windows-x64-msvc"]:
            for profile in ["lgpl", "gpl"]:
                args = compose_configure_args(ROOT, profile, target)
                self.assertIn("--enable-libzimg", args)
                self.assertIn("--disable-hwaccels", args)
                self.assertNotIn("--disable-avfilter", args)
                self.assertEqual("--enable-libx264" in args, profile == "gpl")
                self.assertEqual("--enable-libx265" in args, profile == "gpl")
                self.assertFalse(
                    any(
                        x.startswith(
                            (
                                "--enable-vulkan",
                                "--enable-libplacebo",
                                "--enable-opencl",
                            )
                        )
                        for x in args
                    )
                )

    def test_new_revision_keeps_original_ffmpeg_archive(self):
        lock = load_lock(ROOT / "config/ffmpeg.lock.json")
        self.assertEqual(lock["releaseTag"], "ffmpeg-9.0.1-larix.2")
        self.assertEqual(
            lock["source"]["sha256"],
            "cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635",
        )
        self.assertEqual(lock["libraryVersions"]["avfilter"], 12)
