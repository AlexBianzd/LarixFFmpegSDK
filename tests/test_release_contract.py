from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.common.model import load_lock
from scripts.common.verify_release import (
    RELEASE_CATALOG,
    RELEASE_SUMS,
    _verify_sdk_archive,
    assemble_release_candidate,
    expected_release_asset_names,
    verify_release_directory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ReleaseCandidateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = load_lock(REPOSITORY_ROOT / "config" / "ffmpeg.lock.json")
        source = b"locked source bytes\n"
        self.lock["source"] = {
            "url": "https://example.invalid/ffmpeg-9.0.1.tar.xz",
            "archive": "ffmpeg-9.0.1.tar.xz",
            "size": len(source),
            "sha256": hashlib.sha256(source).hexdigest(),
        }
        (self.root / "ffmpeg-9.0.1.tar.xz").write_bytes(source)
        for name in expected_release_asset_names(self.lock, REPOSITORY_ROOT):
            if name != "ffmpeg-9.0.1.tar.xz":
                (self.root / name).write_bytes((name + "\n").encode("utf-8"))

    def _manifest(
        self, archive: Path, repo_root: Path | None = None
    ) -> dict[str, object]:
        del repo_root
        name = archive.name
        profile = "lgpl" if "-lgpl-" in name else "gpl"
        target_id = "windows-x64-msvc" if name.endswith(".zip") else "macos-arm64"
        return {
            "assetName": name,
            "licenseProfile": profile,
            "releaseTag": self.lock["releaseTag"],
            "source": self.lock["source"],
            "target": {"id": target_id},
        }

    def _assemble_and_verify(self) -> dict[str, object]:
        with mock.patch(
            "scripts.common.verify_release.load_lock", return_value=self.lock
        ), mock.patch(
            "scripts.common.verify_release._verify_sdk_archive",
            side_effect=self._manifest,
        ):
            assemble_release_candidate(
                self.root, str(self.lock["releaseTag"]), REPOSITORY_ROOT
            )
            return verify_release_directory(
                self.root, str(self.lock["releaseTag"]), REPOSITORY_ROOT
            )

    def test_exact_candidate_is_canonical_and_bound_to_all_four_identities(self) -> None:
        catalog = self._assemble_and_verify()
        self.assertEqual(catalog["releaseTag"], self.lock["releaseTag"])
        self.assertEqual(len(catalog["assets"]), 5)
        self.assertEqual(
            {path.name for path in self.root.iterdir()},
            expected_release_asset_names(self.lock, REPOSITORY_ROOT)
            | {RELEASE_CATALOG, RELEASE_SUMS},
        )
        sums = (self.root / RELEASE_SUMS).read_text(encoding="utf-8").splitlines()
        sum_names = [line.split("  ", 1)[1] for line in sums]
        self.assertEqual(sum_names, sorted(sum_names))

    def test_tag_missing_extra_duplicate_and_digest_drift_fail_closed(self) -> None:
        self._assemble_and_verify()
        with mock.patch(
            "scripts.common.verify_release.load_lock", return_value=self.lock
        ), mock.patch(
            "scripts.common.verify_release._verify_sdk_archive",
            side_effect=self._manifest,
        ):
            with self.assertRaises(ValueError):
                verify_release_directory(self.root, "ffmpeg-9.0.1-larix.2", REPOSITORY_ROOT)

            sdk = next(path for path in self.root.iterdir() if path.suffix == ".zip")
            original = sdk.read_bytes()
            sdk.unlink()
            with self.assertRaises(ValueError):
                verify_release_directory(self.root, str(self.lock["releaseTag"]), REPOSITORY_ROOT)
            sdk.write_bytes(original)

            extra = self.root / "unexpected.bin"
            extra.write_bytes(b"unexpected")
            with self.assertRaises(ValueError):
                verify_release_directory(self.root, str(self.lock["releaseTag"]), REPOSITORY_ROOT)
            extra.unlink()

            sdk.write_bytes(original + b"drift")
            with self.assertRaises(ValueError):
                verify_release_directory(self.root, str(self.lock["releaseTag"]), REPOSITORY_ROOT)
            sdk.write_bytes(original)

            catalog_path = self.root / RELEASE_CATALOG
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["assets"].append(dict(catalog["assets"][0]))
            catalog_path.write_text(
                json.dumps(catalog, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                verify_release_directory(self.root, str(self.lock["releaseTag"]), REPOSITORY_ROOT)

    def test_profile_target_and_source_drift_fail_closed(self) -> None:
        self._assemble_and_verify()
        mutations = (
            lambda value: value.__setitem__("licenseProfile", "nonfree"),
            lambda value: value["target"].__setitem__("id", "linux-x64"),
            lambda value: value.__setitem__("source", {}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                def drift(archive: Path, repo_root: Path | None = None) -> dict[str, object]:
                    manifest = self._manifest(archive, repo_root)
                    mutate(manifest)
                    return manifest

                with mock.patch(
                    "scripts.common.verify_release.load_lock", return_value=self.lock
                ), mock.patch(
                    "scripts.common.verify_release._verify_sdk_archive",
                    side_effect=drift,
                ), self.assertRaises(ValueError):
                    verify_release_directory(
                        self.root, str(self.lock["releaseTag"]), REPOSITORY_ROOT
                    )

    def test_archive_requires_build_spdx_license_source_patch_and_config_payloads(self) -> None:
        sdk = self.root / "sdk"
        metadata = sdk / "share" / "larix-ffmpeg-sdk"
        required = (
            metadata / "manifest.json",
            metadata / "sbom.spdx.json",
            metadata / "build.json",
            metadata / "BUILD.txt",
            metadata / "SHA256SUMS",
            metadata / "source.json",
            metadata / "provenance" / "patches" / "README.md",
            metadata / "provenance" / "config" / "common.conf",
            sdk / "LICENSES" / "LarixFFmpegSDK-MIT.txt",
            sdk / "LICENSES" / "FFmpeg-LICENSE.md",
        )
        for path in required:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("contract\n", encoding="utf-8")
        archive = self.root / "placeholder.zip"
        archive.write_bytes(b"archive")
        with mock.patch(
            "scripts.common.verify_release.extract_package", return_value=sdk
        ), mock.patch(
            "scripts.common.verify_release.verify_release_metadata",
            return_value={"assetName": archive.name},
        ):
            self.assertEqual(_verify_sdk_archive(archive, REPOSITORY_ROOT)["assetName"], archive.name)
            for path in required:
                with self.subTest(missing=path.relative_to(sdk)):
                    content = path.read_bytes()
                    path.unlink()
                    with self.assertRaises(ValueError):
                        _verify_sdk_archive(archive, REPOSITORY_ROOT)
                    path.write_bytes(content)


class ReleaseWorkflowContractTests(unittest.TestCase):
    def test_only_final_publish_job_can_write_and_assets_are_immutable(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ffmpeg-9.0.1-larix.1", workflow)
        self.assertRegex(workflow, r"(?m)^  workflow_dispatch:\s*$")
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertIn("contents: read", workflow)
        self.assertIn("  catalog:", workflow)
        self.assertIn("name: release-candidate", workflow)
        self.assertIn("needs: catalog", workflow)
        self.assertIn(
            "github.ref == 'refs/tags/ffmpeg-9.0.1-larix.1'", workflow
        )
        self.assertIn("gh release create", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertNotRegex(workflow, r"(?i)(larger|xlarge|gpu)")
        for runner in ("ubuntu-24.04", "windows-2022", "macos-15"):
            self.assertIn(runner, workflow)


if __name__ == "__main__":
    unittest.main()
