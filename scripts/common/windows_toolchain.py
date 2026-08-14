"""Narrow VS2022 environment discovery for standalone SDK verification."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Callable


def _vcvars_command_line(comspec: str, vcvars: Path) -> str:
    if '"' in comspec or '"' in str(vcvars):
        raise ValueError('vcvars command paths cannot contain double quotes')
    return f'"{comspec}" /d /s /c ""{vcvars}" >nul && set"'


def discover_visual_studio_environment(
    base_environment: dict[str, str] | None = None,
    *,
    exists: Callable[[Path], bool] = Path.is_file,
    standard_vswhere: Path | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    environment = dict(base_environment or os.environ)
    vswhere = Path(
        environment.get(
            "LARIX_VSWHERE",
            str(
                standard_vswhere
                or Path(environment.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
                / "Microsoft Visual Studio/Installer/vswhere.exe"
            ),
        )
    )
    if not exists(vswhere):
        raise RuntimeError(f"VS2022 vswhere is missing: {vswhere}")
    discovered = subprocess.run(
        [
            str(vswhere), "-latest", "-products", "*", "-version", "[17.0,18.0)",
            "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property", "installationPath",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    if not discovered:
        raise RuntimeError("VS2022 x64 C++ tools were not discovered")
    vcvars = Path(discovered) / "VC/Auxiliary/Build/vcvars64.bat"
    if not exists(vcvars):
        raise RuntimeError(f"VS2022 vcvars64 is missing: {vcvars}")
    output = subprocess.run(
        _vcvars_command_line(environment.get("ComSpec", "cmd.exe"), vcvars),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            environment[key] = value
    tools = environment.get("VCToolsVersion", "").strip().rstrip("\\/")
    windows_sdk = environment.get("WindowsSDKVersion", "").strip().rstrip("\\/")
    if not tools or not windows_sdk:
        raise RuntimeError("VS2022 environment has incomplete toolchain identity")
    return environment, {"compiler": f"MSVC {tools}", "windowsSdk": windows_sdk}


def require_matching_toolchain(
    declared: dict[str, str], discovered: dict[str, str]
) -> None:
    if declared != discovered:
        raise ValueError(
            f"SDK toolchain identity mismatch: declared={declared}, discovered={discovered}"
        )
