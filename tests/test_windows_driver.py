from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from scripts.common.model import load_target
from scripts.common.release_manifest import WINDOWS_RUNTIME_FILES
from scripts.common.verify_sdk import (
    _require_archive_identity,
    _require_inspection_report,
    _required_tool,
)
from scripts.common.windows_toolchain import (
    _vcvars_command_line,
    discover_visual_studio_environment,
    require_matching_toolchain,
)
from tests.fixtures.generate_media import generate_fixtures


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STABLE_ROOT_HELPER = REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "stable_build_root.ps1"


class StableBuildRootTests(unittest.TestCase):
    def _powershell(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe", "-NoLogo", "-NoProfile",
                "-ExecutionPolicy", "Bypass", "-Command", command,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def _drive_is_free(self) -> bool:
        result = self._powershell("if ((Get-PSDrive R -ErrorAction SilentlyContinue) -or ((subst.exe) -match '(?im)^R:\\\\: =>')) { exit 1 }")
        return result.returncode == 0

    def _wait_for_free_drive(self) -> bool:
        for _ in range(40):
            if self._drive_is_free():
                return True
            time.sleep(0.05)
        return False

    def setUp(self) -> None:
        if not self._wait_for_free_drive():
            self.skipTest("R: is already occupied")

    def tearDown(self) -> None:
        self.assertTrue(self._wait_for_free_drive(), "test left R: occupied")

    def test_dot_source_has_no_mapping_side_effects(self) -> None:
        completed = self._powershell(
            "$before=(@(& subst.exe) -join [Environment]::NewLine); "
            + f". '{STABLE_ROOT_HELPER}'; "
            + "$after=(@(& subst.exe) -join [Environment]::NewLine); "
            + "if($before -ne $after){throw 'dot-source changed subst mappings'}; "
            + "if(-not(Get-Command Invoke-LarixStableBuildRoot -ErrorAction SilentlyContinue)){throw 'helper missing'}"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_real_drive_without_running_or_releasing_it(self) -> None:
        if not self._wait_for_free_drive(): self.skipTest("R: is already occupied")
        with tempfile.TemporaryDirectory() as temporary:
            completed = self._powershell(f". '{STABLE_ROOT_HELPER}'; New-PSDrive -Name R -PSProvider FileSystem -Root '{temporary}' -Scope Global|Out-Null; try {{ Invoke-LarixStableBuildRoot -PhysicalRoot 'C:\\tmp' -Action {{ throw 'BODY_RAN' }} }} finally {{ if(-not(Get-PSDrive R -ErrorAction SilentlyContinue)){{throw 'foreign drive removed'}}; Remove-PSDrive R -Scope Global }}")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("already occupied", completed.stderr)
        self.assertNotIn("BODY_RAN", completed.stderr)

    def test_rejects_existing_subst_and_never_releases_foreign_mapping(self) -> None:
        if not self._wait_for_free_drive(): self.skipTest("R: is already occupied")
        with tempfile.TemporaryDirectory() as temporary:
            try:
                subprocess.run(["subst.exe", "R:", temporary], check=True)
                completed = self._powershell(f". '{STABLE_ROOT_HELPER}'; Invoke-LarixStableBuildRoot -PhysicalRoot 'C:\\tmp' -Action {{ throw 'BODY_RAN' }}")
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("already occupied", completed.stderr)
                self.assertNotIn("BODY_RAN", completed.stderr)
                self.assertIn("R:\\: =>", subprocess.run(["subst.exe"], check=True, capture_output=True, text=True).stdout)
            finally:
                subprocess.run(["subst.exe", "R:", "/d"], check=False)

    def test_subst_create_failure_is_fail_closed_and_has_recovery_hint(self) -> None:
        if not self._wait_for_free_drive(): self.skipTest("R: is already occupied")
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "subst-fail.cmd"
            fake.write_text("@echo off\r\nexit /b 19\r\n", encoding="utf-8")
            completed = self._powershell(f". '{STABLE_ROOT_HELPER}'; Invoke-LarixStableBuildRoot -PhysicalRoot '{temporary}' -SubstCommand '{fake}' -Action {{ throw 'BODY_RAN' }}")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("failed to create", completed.stderr)
        self.assertIn("subst R: /d", completed.stderr)
        self.assertNotIn("BODY_RAN", completed.stderr)

    def test_body_failure_propagates_and_owned_mapping_is_removed_once(self) -> None:
        if not self._wait_for_free_drive(): self.skipTest("R: is already occupied")
        with tempfile.TemporaryDirectory() as temporary:
            completed = self._powershell(f". '{STABLE_ROOT_HELPER}'; Invoke-LarixStableBuildRoot -PhysicalRoot '{temporary}' -Action {{ param($root) if($root -ne 'R:\\'){{throw 'BAD_ROOT'}}; Write-Output 'ROOT_OK'; throw 'BODY_SENTINEL' }}")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("BODY_SENTINEL", completed.stderr)
        self.assertIn("ROOT_OK", completed.stdout)
        self.assertTrue(self._wait_for_free_drive())

    def test_cleanup_failure_reports_recovery_and_leaves_mapping_visible(self) -> None:
        if not self._wait_for_free_drive(): self.skipTest("R: is already occupied")
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "subst-cleanup-fail.cmd"
            fake.write_text(
                "@echo off\r\nif %2==/d exit /b 23\r\n"
                "subst.exe %*\r\nexit /b %errorlevel%\r\n",
                encoding="utf-8",
            )
            try:
                completed = self._powershell(f". '{STABLE_ROOT_HELPER}'; Invoke-LarixStableBuildRoot -PhysicalRoot '{temporary}' -SubstCommand '{fake}' -Action {{ param($root) 'BODY_OK' }}")
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("failed to remove", completed.stderr)
                self.assertIn("subst R: /d", completed.stderr)
                self.assertIn("R:\\: =>", subprocess.run(["subst.exe"], check=True, capture_output=True, text=True).stdout)
            finally:
                subprocess.run(["subst.exe", "R:", "/d"], check=False)

    def test_cleanup_success_code_with_stale_mapping_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "subst-stale.cmd"
            fake.write_text(
                "@echo off\r\nif %2==/d exit /b 0\r\n"
                "subst.exe %*\r\nexit /b %errorlevel%\r\n",
                encoding="utf-8",
            )
            try:
                completed = self._powershell(
                    f". '{STABLE_ROOT_HELPER}'; Invoke-LarixStableBuildRoot "
                    f"-PhysicalRoot '{temporary}' -SubstCommand '{fake}' "
                    "-Action { param($root) 'BODY_OK' }"
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("failed to remove", completed.stderr)
                self.assertIn("subst R: /d", completed.stderr)
            finally:
                subprocess.run(["subst.exe", "R:", "/d"], check=False)


class WindowsToolchainTests(unittest.TestCase):
    def test_vcvars_raw_command_line_preserves_a_bat_path_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="larix vcvars ") as temporary:
            vcvars = Path(temporary) / "fake vcvars64.bat"
            vcvars.write_text(
                "@echo off\r\n"
                "set VCToolsVersion=14.44.35207\r\n"
                "set WindowsSDKVersion=10.0.26100.0\\\r\n",
                encoding="utf-8",
            )
            command_line = _vcvars_command_line(os.environ["ComSpec"], vcvars)
            self.assertIsInstance(command_line, str)
            self.assertEqual(
                command_line,
                f'"{os.environ["ComSpec"]}" /d /s /c ""{vcvars}" >nul && set"',
            )
            completed = subprocess.run(
                command_line,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("VCToolsVersion=14.44.35207", completed.stdout)

    def test_required_tool_uses_discovered_environment_and_explicit_override(self) -> None:
        with mock.patch("shutil.which", return_value="C:/Tools/cmake.exe") as which:
            self.assertEqual(
                _required_tool("cmake", {"PATH": "C:/Tools"}),
                "C:/Tools/cmake.exe",
            )
        which.assert_called_once_with("cmake", path="C:/Tools")
        self.assertEqual(
            _required_tool("cmake", {"PATH": ""}, "D:/CMake/cmake.exe"),
            "D:/CMake/cmake.exe",
        )

    def test_discovers_vs2022_with_normal_path_and_returns_exact_identity(self) -> None:
        vswhere = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
        installation = Path("C:/Program Files/Microsoft Visual Studio/2022/Community")
        vcvars = installation / "VC/Auxiliary/Build/vcvars64.bat"
        outputs = [
            mock.Mock(returncode=0, stdout=str(installation) + "\n", stderr=""),
            mock.Mock(
                returncode=0,
                stdout=(
                    "Path=C:\\VS\\bin;C:\\Windows\\System32\n"
                    "VCToolsVersion=14.44.35207\n"
                    "WindowsSDKVersion=10.0.26100.0\\\n"
                ),
                stderr="",
            ),
        ]
        exists = lambda path: Path(path) in {vswhere, vcvars}
        with mock.patch("subprocess.run", side_effect=outputs) as run:
            environment, identity = discover_visual_studio_environment(
                {}, exists=exists, standard_vswhere=vswhere
            )
        self.assertEqual(environment["VCToolsVersion"], "14.44.35207")
        self.assertEqual(
            identity,
            {"compiler": "MSVC 14.44.35207", "windowsSdk": "10.0.26100.0"},
        )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(Path(run.call_args_list[0].args[0][0]), vswhere)
        self.assertIn(str(vcvars), run.call_args_list[1].args[0])

    def test_rejects_declared_toolchain_drift(self) -> None:
        with self.assertRaises(ValueError):
            require_matching_toolchain(
                {"compiler": "MSVC 14.44", "windowsSdk": "10.0.26100.0"},
                {"compiler": "MSVC 14.43", "windowsSdk": "10.0.22621.0"},
            )


class WindowsWrapperTests(unittest.TestCase):
    _PROXY = REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "larix-msvc-cl.cmd"
    _FAKE_CL = REPOSITORY_ROOT / "tests" / "fixtures" / "fake-msvc-cl.cmd"
    _IDENTITY = "Microsoft (R) C/C++ Optimizing Compiler Version 19.44.35221 for x64"

    def test_driver_separates_virtual_work_paths_from_physical_output(self) -> None:
        source = (REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("$physicalOutput = [IO.Path]::GetFullPath($OutputRoot)", source)
        self.assertIn("Invoke-LarixStableBuildRoot -PhysicalRoot $physicalOutput", source)
        self.assertIn("param($stableRoot)", source)
        self.assertIn("$output = $stableRoot", source)
        self.assertIn("$source = Join-Path $output", source)
        self.assertIn("$install = Join-Path $output", source)
        self.assertIn("$build = Join-Path $output", source)
        self.assertIn("$stage = Join-Path $output", source)
        self.assertIn("$package = Join-Path $output", source)
        self.assertIn("$physicalSource = Join-Path $physicalOutput", source)
        self.assertIn("$physicalInstall = Join-Path $physicalOutput", source)
        self.assertIn("$physicalBuild = Join-Path $physicalOutput", source)
        self.assertIn("$physicalStage = Join-Path $physicalOutput", source)
        self.assertIn("[IO.Path]::GetFullPath($physicalSource)", source)
        self.assertIn("[IO.Path]::GetFullPath($physicalInstall)", source)
        self.assertIn("[IO.Path]::GetFullPath($physicalBuild)", source)
        self.assertIn("[IO.Path]::GetFullPath($physicalStage)", source)
        self.assertIn("[IO.Path]::GetFullPath($physicalOutput)", source)
        self.assertIn("subst R: /d", STABLE_ROOT_HELPER.read_text(encoding="utf-8"))

    def _proxy_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["LARIX_REAL_CL"] = str(self._FAKE_CL)
        environment["LARIX_MSVC_IDENTITY"] = self._IDENTITY
        environment["MSYS2_ARG_CONV_EXCL"] = "*"
        return environment

    def _run_proxy(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self._PROXY), *arguments],
            check=False, capture_output=True, text=True,
            env=self._proxy_environment())

    def test_localized_msvc_proxy_only_intercepts_configure_identity_probes(self) -> None:
        for arguments in ((), ("-nologo-",)):
            with self.subTest(arguments=arguments):
                completed = self._run_proxy(*arguments)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout.strip(), self._IDENTITY)
        for arguments in (
            ("-nologo-", "extra"),
            ("/c", "source with space.c"),
        ):
            with self.subTest(arguments=arguments):
                completed = self._run_proxy(*arguments)
                self.assertEqual(completed.returncode, 37, completed.stderr)
                self.assertEqual(
                    completed.stdout.strip(),
                    "FAKE_MSVC_DELEGATE:[" + "][".join(arguments) + "]")

    def test_msys2_bash_resolves_and_invokes_the_cmd_proxy_from_path(self) -> None:
        bash = Path("C:/msys64/usr/bin/bash.exe")
        if not bash.is_file():
            self.skipTest("real MSYS2 Bash is unavailable")
        proxy_directory = "/" + self._PROXY.parent.as_posix().replace(":", "", 1)
        completed = subprocess.run(
            [str(bash), "--noprofile", "--norc", "-lc",
             f"export PATH='{proxy_directory}:/usr/bin'; "
             "command -v larix-msvc-cl.cmd; larix-msvc-cl.cmd -nologo-"],
            check=False, capture_output=True, text=True,
            env=self._proxy_environment())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = completed.stdout.splitlines()
        self.assertTrue(lines[0].endswith("/larix-msvc-cl.cmd"), lines)
        self.assertEqual(lines[1], self._IDENTITY)

    def test_build_shell_restores_msys_path_conversion_before_configure(self) -> None:
        bash = Path("C:/msys64/usr/bin/bash.exe")
        if not bash.is_file():
            self.skipTest("real MSYS2 Bash is unavailable")
        proxy_directory = "/" + self._PROXY.parent.as_posix().replace(":", "", 1)
        environment = self._proxy_environment()
        completed = subprocess.run(
            [
                str(bash), "--noprofile", "--norc", "-lc",
                f"export PATH='{proxy_directory}:/usr/bin'; "
                "unset MSYS2_ARG_CONV_EXCL; "
                "larix-msvc-cl.cmd /c /c/Temp/source.c",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(completed.returncode, 37, completed.stderr)
        self.assertEqual(
            completed.stdout.strip(),
            "FAKE_MSVC_DELEGATE:[C:/][C:/Temp/source.c]",
        )

        environment["MSYS2_ARG_CONV_EXCL"] = (
            "/Brepro;/PDBALTPATH:;/pathmap:;/experimental:"
        )
        selective = subprocess.run(
            [
                str(bash), "--noprofile", "--norc", "-lc",
                f"export PATH='{proxy_directory}:/usr/bin'; "
                "larix-msvc-cl.cmd /Brepro /c/Temp/source.c",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(selective.returncode, 37, selective.stderr)
        self.assertEqual(
            selective.stdout.strip(),
            "FAKE_MSVC_DELEGATE:[/Brepro][C:/Temp/source.c]",
        )

        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "export MSYS2_ARG_CONV_EXCL=''/Brepro;/PDBALTPATH:;/pathmap:;/experimental:;./src''; "
            "export PATH={0}:$PATH",
            source,
        )
        self.assertNotIn("$installMsys", source)

    def test_driver_exposes_real_cl_and_proxy_identity_to_msys(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("$env:LARIX_REAL_CL = $cl", source)
        self.assertIn("$env:LARIX_MSVC_IDENTITY =", source)
        self.assertIn("Split-Path $msvcClProxy", source)
        self.assertIn("--cc=larix-msvc-cl.cmd", source)

    def test_driver_injects_absolute_pathmaps_through_a_clean_cl_environment(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("if ($env:CL)", source)
        self.assertRegex(
            source,
            r"\$absoluteCompilerFlags = @\(\s*'/experimental:deterministic',\s*'/Brepro'\s*\) \+ \$absolutePathMaps",
        )
        self.assertIn("$quotedAbsoluteCompilerFlags = @(", source)
        self.assertIn("[char]34 + $_ + [char]34", source)
        self.assertIn("$env:CL = ($quotedAbsoluteCompilerFlags -join ' ')", source)
        clear = "Remove-Item Env:CL"
        self.assertEqual(source.count(clear), 1)
        self.assertIn(
            "$buildExitCode = -1\ntry {\n"
            "    $env:CL = ($quotedAbsoluteCompilerFlags -join ' ')\n"
            "    & $bash --noprofile --norc -lc $shellCommand",
            source,
        )
        build_scope = source[
            source.index("$buildExitCode = -1"):
            source.index("if ($buildExitCode -ne 0)")
        ]
        self.assertIn("try {", build_scope)
        self.assertIn("finally {", build_scope)
        self.assertIn("$buildExitCode = $LASTEXITCODE", build_scope)
        self.assertIn(clear, build_scope)
        for path, replacement in (
            ("$sourceJunction", "larix-source"),
            ("$source", "larix-source"),
            ("$build", "larix-build"),
            ("$install", "larix-install"),
            ("$output", "larix-output"),
        ):
            self.assertIn(
                "('/pathmap:' + " + path + " + '=" + replacement + "')", source
            )
        configure_scope = source[
            source.index("$deterministicCFlags = @("):
            source.index("$sourceJunction =", source.index("$deterministicCFlags = @("))
        ]
        self.assertNotIn("('/pathmap:' + $source", configure_scope)
        self.assertIn("'/pathmap:./src=larix-source'", configure_scope)

    def test_driver_reads_the_runtime_inventory_from_the_locked_contract(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("WINDOWS_RUNTIME_FILES", source)
        self.assertIn("$runtimeJson", source)
        self.assertIn("print(json.dumps(WINDOWS_RUNTIME_FILES))", source)
        self.assertNotIn("removeprefix", source)
        self.assertNotIn("avcodec-62.dll", source)
        self.assertIn("'ffprobe.exe' = 'ffprobe_g.pdb'", source)
        self.assertIn("('bin/' + $component + '.lib')", source)
        self.assertNotIn("('lib/' + $component + '.lib')", source)
        command = (
            f"& '{os.sys.executable}' -c "
            "'import json; from scripts.common.release_manifest import "
            "WINDOWS_RUNTIME_FILES; print(json.dumps(WINDOWS_RUNTIME_FILES))'"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            [
                "bin/avcodec-63.dll",
                "bin/avformat-63.dll",
                "bin/avutil-61.dll",
                "bin/ffprobe.exe",
                "bin/swresample-7.dll",
                "bin/swscale-10.dll",
            ],
        )

    def test_configure_json_is_flattened_to_a_typed_string_array(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "[string[]]$configure = $configureJson | ConvertFrom-Json", source
        )
        self.assertNotIn(
            "$configure = @($configureJson | ConvertFrom-Json)", source
        )
        command = (
            "$json=@('--a','--b')|ConvertTo-Json -Compress; "
            "[string[]]$values=$json|ConvertFrom-Json; "
            "[Console]::WriteLine($values.Count.ToString()+'|'+($values -join '|'))"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "2|--a|--b")

    def test_configure_target_is_a_native_argument_not_an_inline_python_literal(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        line = next(
            value for value in source.splitlines()
            if "compose_configure_args" in value
        )
        self.assertIn("sys.argv[3]", line)
        self.assertNotIn('"windows-x64-msvc"', line)
        self.assertRegex(
            line, r"\$repo\s+\$Profile\s+'windows-x64-msvc'\s*$"
        )

    def test_asset_identity_paths_are_native_arguments_not_python_literals(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        line = next(
            value for value in source.splitlines()
            if "$asset = & $python" in value
        )
        for index in ("1", "2", "3"):
            self.assertIn(f"sys.argv[{index}]", line)
        self.assertNotIn('root/"config', line)
        self.assertIn("(Join-Path $repo 'config/ffmpeg.lock.json')", line)
        self.assertIn(
            "(Join-Path $repo 'config/targets/windows-x64-msvc.json')", line
        )
        self.assertTrue(line.endswith("$Profile"), line)

    def test_identity_probes_are_locally_nonterminating_without_weakening_build_failures(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        scope_marker = "$previousErrorActionPreference = $ErrorActionPreference"
        self.assertIn(scope_marker, source)
        start = source.index(scope_marker)
        end = source.index("Write-Host $clIdentity", start)
        probe_scope = source[start:end]
        self.assertIn("$ErrorActionPreference = 'Continue'", probe_scope)
        self.assertIn("try {", probe_scope)
        self.assertIn("finally {", probe_scope)
        self.assertIn(
            "$ErrorActionPreference = $previousErrorActionPreference", probe_scope
        )
        for identity, invocation in (
            ("clIdentity", "& $cl 2>&1 | Select-Object -First 1"),
            ("linkIdentity", "& $link 2>&1 | Select-Object -First 1"),
            ("libIdentity", "& $lib 2>&1 | Select-Object -First 1"),
            ("nmakeIdentity", "& $nmake /? 2>&1 | Select-Object -First 1"),
        ):
            self.assertIn(f"${identity} = {invocation}", probe_scope)
            self.assertIn(f"Write-Host ${identity}", source[end:])

        for invocation, failure in (
            (
                "& $python -m scripts.common.source",
                "if ($LASTEXITCODE -ne 0) { throw 'Verified source preparation failed.' }",
            ),
            (
                "& $bash --noprofile --norc -lc $shellCommand",
                "if ($buildExitCode -ne 0) { throw 'FFmpeg MSVC build failed.' }",
            ),
            (
                "& (Join-Path $PSScriptRoot 'inspect.ps1')",
                "if ($LASTEXITCODE -ne 0) { throw 'Windows binary inspection failed.' }",
            ),
            (
                "& $python -m scripts.common.package",
                "if ($LASTEXITCODE -ne 0) { throw 'SDK packaging failed.' }",
            ),
            (
                "& $python -m scripts.common.verify_sdk",
                "if ($LASTEXITCODE -ne 0) { throw 'SDK relocation verification failed.' }",
            ),
        ):
            self.assertIn(invocation, source)
            self.assertIn(failure, source)

    def test_noprofile_build_exports_msys_and_nasm_tool_directories(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("& $bash --noprofile --norc -lc $shellCommand", source)
        self.assertIn("$cmp = Require-File 'MSYS2 cmp'", source)
        for tool in ("bash", "make", "nasm", "cmp"):
            self.assertRegex(source, rf"Split-Path\s+\${tool}")
        self.assertRegex(source, r"export PATH=\{0\}:\$PATH")
        self.assertIn("$toolPathPrefix", source)
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("MSYS2 Bash, GNU make and diffutils", readme)

    def test_default_output_root_follows_the_selected_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            platform = scripts / "platforms" / "windows"
            platform.mkdir(parents=True)
            (scripts / "build-windows.ps1").write_bytes(
                (REPOSITORY_ROOT / "scripts" / "build-windows.ps1").read_bytes()
            )
            fake = platform / "build.ps1"
            fake.write_text(
                "param($RepoRoot,$Profile,$Configuration,$OutputRoot)\n"
                "[Console]::WriteLine($Profile + '|' + $OutputRoot)\n",
                encoding="utf-8",
                newline="\n",
            )
            completed = subprocess.run(
                [
                    "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(scripts / "build-windows.ps1"), "-Profile", "gpl",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            profile, output = completed.stdout.strip().split("|", 1)
            self.assertEqual(profile, "gpl")
            self.assertEqual(Path(output), root / "build" / "windows-gpl")


class DeterministicFixtureTests(unittest.TestCase):
    def test_generates_repo_owned_timed_avi_and_pcm_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video, audio = generate_fixtures(Path(temporary))
            avi = video.read_bytes()
            self.assertEqual(video.name, "video.avi")
            self.assertEqual(avi[:4], b"RIFF")
            self.assertEqual(avi[8:12], b"AVI ")
            self.assertIn(b"avih", avi)
            self.assertIn(b"strh", avi)
            self.assertIn(b"movi", avi)
            wav = audio.read_bytes()
            self.assertEqual(wav[:4], b"RIFF")
            self.assertEqual(wav[8:12], b"WAVE")
            self.assertIn(b"fmt ", wav)
            self.assertIn(b"data", wav)

    def test_consumer_uses_ffmpeg_owned_audio_buffers_and_runtime_media_argument(self) -> None:
        smoke = (REPOSITORY_ROOT / "tests" / "consumer" / "smoke.c").read_text(
            encoding="utf-8"
        )
        audio_scope = smoke[
            smoke.index("static int convert_audio(void)"):
            smoke.index("int main(int argc, char** argv)")
        ]
        cmake = (
            REPOSITORY_ROOT / "tests" / "consumer" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(audio_scope.count("av_samples_alloc("), 2)
        self.assertEqual(audio_scope.count("av_freep("), 2)
        self.assertIn("if (argc != 2) return 2;", smoke)
        self.assertNotIn("LARIX_FFMPEG_TEST_MEDIA", smoke)
        self.assertNotIn("LARIX_FFMPEG_TEST_MEDIA", cmake)

    def test_consumer_uses_ffmpeg_owned_padded_pixel_buffers(self) -> None:
        smoke = (REPOSITORY_ROOT / "tests" / "consumer" / "smoke.c").read_text(
            encoding="utf-8"
        )
        pixel_scope = smoke[
            smoke.index("static int convert_pixels(void)"):
            smoke.index("static int convert_audio(void)")
        ]
        self.assertIn("#include <libavutil/imgutils.h>", smoke)
        self.assertIn("av_image_alloc(", pixel_scope)
        self.assertIn("av_freep(&output[0]);", pixel_scope)
        self.assertNotIn("uint8_t rgb[12]", pixel_scope)
        self.assertNotIn("uint8_t bgr[12]", pixel_scope)

    def test_cmake_targets_bind_locked_runtime_abis_and_existing_locations(self) -> None:
        config = (
            REPOSITORY_ROOT / "cmake" / "LarixFFmpegSDKConfig.cmake.in"
        ).read_text(encoding="utf-8")
        consumer = (
            REPOSITORY_ROOT / "tests" / "consumer" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("set(_larix_ffmpeg_dll_versions 61 63 63 7 10)", config)
        self.assertIn("if(NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)", config)
        self.assertIn(
            "if(NOT EXISTS \"${_implib}\" OR NOT EXISTS \"${_runtime}\")",
            config,
        )
        self.assertIn("get_target_property(_runtime", consumer)
        self.assertIn("get_target_property(_implib", consumer)
        self.assertIn("if(NOT EXISTS \"${_runtime}\" OR NOT EXISTS \"${_implib}\")", consumer)


class FinalArchiveVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = {
            "compiler": "MSVC 14.44.35207",
            "windowsSdk": "10.0.26100.0",
        }
        self.dependencies = {
            path: ["KERNEL32.dll"] for path in WINDOWS_RUNTIME_FILES
        }
        self.manifest = {
            "assetName": "expected.zip",
            "toolchain": self.identity,
            "runtimeDependencies": self.dependencies,
            "target": load_target(
                REPOSITORY_ROOT / "config" / "targets" / "windows-x64-msvc.json"
            ),
        }

    def test_archive_name_is_bound_to_the_manifest(self) -> None:
        with self.assertRaises(ValueError):
            _require_archive_identity(Path("renamed.zip"), self.manifest)
        _require_archive_identity(Path("expected.zip"), self.manifest)

    def test_reobserved_dependencies_and_toolchain_must_match(self) -> None:
        report = {
            "runtimeDependencies": self.dependencies,
            "toolchain": self.identity,
        }
        _require_inspection_report(report, self.manifest)
        dependency_drift = json.loads(json.dumps(report))
        dependency_drift["runtimeDependencies"]["bin/ffprobe.exe"] = [
            "UNDECLARED.dll"
        ]
        with self.assertRaises(ValueError):
            _require_inspection_report(dependency_drift, self.manifest)
        toolchain_drift = json.loads(json.dumps(report))
        toolchain_drift["toolchain"]["compiler"] = "MSVC 14.43"
        with self.assertRaises(ValueError):
            _require_inspection_report(toolchain_drift, self.manifest)

    def test_final_verifier_requests_and_consumes_an_inspection_report(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts" / "common" / "verify_sdk.py"
        ).read_text(encoding="utf-8")
        self.assertIn("-ReportPath", source)
        self.assertIn("_require_inspection_report(report, manifest)", source)


if __name__ == "__main__":
    unittest.main()
