from __future__ import annotations

from pathlib import Path
import re
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "verify.yml"


class WorkflowPolicyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(WORKFLOW.is_file(), "missing .github/workflows/verify.yml")
        self.source = WORKFLOW.read_text(encoding="utf-8")

    def test_only_pull_request_and_manual_verification_are_enabled(self) -> None:
        self.assertRegex(self.source, r"(?m)^on:\s*$")
        self.assertRegex(self.source, r"(?m)^  pull_request:\s*$")
        self.assertRegex(self.source, r"(?m)^  workflow_dispatch:\s*$")
        self.assertNotRegex(self.source, r"(?m)^  (push|schedule):")
        self.assertIn("full:", self.source)
        self.assertIn("type: boolean", self.source)
        self.assertIn("default: false", self.source)

    def test_permissions_and_runner_labels_are_free_public_runner_only(self) -> None:
        self.assertRegex(self.source, r"(?ms)^permissions:\s*\n  contents: read\s*$")
        self.assertIn("runs-on: windows-2022", self.source)
        self.assertIn("runs-on: macos-15", self.source)
        self.assertNotIn("runs-on: ubuntu-24.04", self.source)
        lowered = self.source.lower()
        for forbidden in (
            "self-hosted",
            "xlarge",
            "large",
            "packages: write",
            "contents: write",
            "id-token: write",
            "docker://",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_full_jobs_are_manual_and_call_only_repository_build_drivers(self) -> None:
        manual_gate = "github.event_name == 'workflow_dispatch' && inputs.full"
        self.assertGreaterEqual(self.source.count(manual_gate), 2)
        self.assertIn("scripts/build-windows.ps1", self.source)
        self.assertIn("./scripts/build-macos.sh", self.source)
        for forbidden in (
            "--enable-gpl",
            "--disable-nonfree",
            "--extra-cflags",
            "--extra-ldflags",
            "./configure",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_actions_are_sha_pinned_and_artifacts_expire_after_one_day(self) -> None:
        uses = re.findall(r"(?m)^\s*- uses:\s*([^\s]+)\s*$", self.source)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(
                action,
                r"^(actions/(checkout|setup-python|upload-artifact)|"
                r"msys2/setup-msys2)@[0-9a-f]{40}$",
            )
        required = {
            "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "msys2/setup-msys2@66cd2cce69caa17b53920067426061ca1de3a884",
        }
        self.assertTrue(required <= set(uses))
        self.assertEqual(self.source.count("retention-days: 1"), 2)
        self.assertNotIn("retention-days: 2", self.source)

    def test_fast_tests_run_on_pull_requests_without_building_packages(self) -> None:
        self.assertIn("python -m unittest discover -s tests -p 'test_*.py' -v", self.source)
        fast_start = self.source.index("  fast-tests:")
        full_start = min(
            self.source.index("  windows-sdk:"),
            self.source.index("  macos-sdk:"),
        )
        fast_job = self.source[fast_start:full_start]
        self.assertIn("runs-on: windows-2022", fast_job)
        self.assertNotIn("upload-artifact", fast_job)
        self.assertNotIn("build-windows.ps1", fast_job)
        self.assertNotIn("build-macos.sh", fast_job)

    def test_windows_job_installs_hash_verified_official_nasm(self) -> None:
        windows_start = self.source.index("  windows-sdk:")
        macos_start = self.source.index("  macos-sdk:")
        windows_job = self.source[windows_start:macos_start]
        self.assertIn(
            "https://www.nasm.us/pub/nasm/releasebuilds/3.02/win64/"
            "nasm-3.02-installer-x64.exe",
            windows_job,
        )
        self.assertIn(
            "0DDB40310861EB29F4D649FEB9466779982A2D251C0DB2B9CF0D21CF591171F3",
            windows_job,
        )
        self.assertIn("Get-FileHash", windows_job)
        self.assertIn("Start-Process", windows_job)
        self.assertIn("id: msys2", windows_job)
        self.assertIn("release: false", windows_job)
        self.assertIn("update: false", windows_job)
        self.assertIn("make diffutils", windows_job)
        self.assertIn("steps.msys2.outputs.msys2-location", windows_job)
        for variable in (
            "LARIX_MSYS2_BASH",
            "LARIX_MSYS2_MAKE",
            "LARIX_MSYS2_CMP",
        ):
            self.assertIn(variable, windows_job)
        self.assertLess(
            windows_job.index("Get-FileHash"),
            windows_job.index("scripts/build-windows.ps1"),
        )


if __name__ == "__main__":
    unittest.main()
