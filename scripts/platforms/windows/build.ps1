[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][ValidateSet('lgpl', 'gpl')][string]$Profile,
    [Parameter(Mandatory = $true)][ValidateSet('Release')][string]$Configuration,
    [Parameter(Mandatory = $true)][string]$OutputRoot
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'stable_build_root.ps1')

function Require-File([string]$Description, [string[]]$Candidates) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw ('Required {0} was not found. Checked: {1}' -f $Description, ($Candidates -join ', '))
}
function Require-Command([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw ('Required command is unavailable after vcvars64: ' + $Name) }
    return $command.Source
}
function Msys-Path([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).Replace('\', '/')
    return '/' + $full.Substring(0, 1).ToLowerInvariant() + $full.Substring(2)
}
function Bash-Quote([string]$Value) {
    if ($Value.Contains([char]39)) { throw ('Bash argument contains an unsupported quote: ' + $Value) }
    return [char]39 + $Value + [char]39
}

$repo = [IO.Path]::GetFullPath($RepoRoot)
$physicalOutput = [IO.Path]::GetFullPath($OutputRoot)
$vswhere = Require-File 'VS2022 vswhere' @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'))
$installation = (& $vswhere -latest -products '*' -version '[17.0,18.0)' `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
if (-not $installation) { throw 'Visual Studio 2022 with x64 C++ tools was not found.' }
$vcvars = Require-File 'VS2022 vcvars64.bat' @((Join-Path $installation 'VC/Auxiliary/Build/vcvars64.bat'))
$environmentLines = & $env:ComSpec /d /s /c ('"' + $vcvars + '" >nul && set')
if ($LASTEXITCODE -ne 0) { throw 'vcvars64.bat failed.' }
foreach ($line in $environmentLines) {
    $separator = $line.IndexOf('=')
    if ($separator -gt 0) {
        [Environment]::SetEnvironmentVariable(
            $line.Substring(0, $separator), $line.Substring($separator + 1), 'Process')
    }
}
if ($env:CL) {
    throw 'A pre-existing CL environment variable is prohibited for reproducible builds.'
}
$cl = Require-Command 'cl.exe'
$msvcClProxy = Require-File 'Larix localized MSVC compiler proxy' @(
    (Join-Path $PSScriptRoot 'larix-msvc-cl.cmd'))
$clVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($cl).ProductVersion
$clVersionMatch = [regex]::Match($clVersion, '^[0-9]+\.[0-9]+\.[0-9]+')
if (-not $clVersionMatch.Success) {
    throw ('Unable to determine the MSVC compiler version from: ' + $clVersion)
}
$env:LARIX_REAL_CL = $cl
$env:LARIX_MSVC_IDENTITY = (
    'Microsoft (R) C/C++ Optimizing Compiler Version {0} for x64' -f $clVersionMatch.Value)
$link = Require-Command 'link.exe'
$lib = Require-Command 'lib.exe'
$nmake = Require-Command 'nmake.exe'
$null = Require-Command 'dumpbin.exe'
$nasmCommand = Get-Command nasm.exe -ErrorAction SilentlyContinue
$cmakeCommand = Get-Command cmake.exe -ErrorAction SilentlyContinue
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
$nasmCandidate = if ($nasmCommand) { $nasmCommand.Source } else { $null }
$cmakeCandidate = if ($cmakeCommand) { $cmakeCommand.Source } else { $null }
$pythonCandidate = if ($pythonCommand) { $pythonCommand.Source } else { $null }
$nasm = Require-File 'NASM' @($env:LARIX_NASM, 'C:/Program Files/NASM/nasm.exe', $nasmCandidate)
$bash = Require-File 'MSYS2 Bash' @($env:LARIX_MSYS2_BASH, 'C:/msys64/usr/bin/bash.exe')
$make = Require-File 'MSYS2 make' @($env:LARIX_MSYS2_MAKE, 'C:/msys64/usr/bin/make.exe')
$cmp = Require-File 'MSYS2 cmp' @((Join-Path (Split-Path $bash) 'cmp.exe'))
$cmake = Require-File 'CMake' @($env:LARIX_CMAKE, 'C:/Program Files/CMake/bin/cmake.exe', $cmakeCandidate)
$python = Require-File 'Python 3.12+' @($env:LARIX_PYTHON, $pythonCandidate)
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $clIdentity = & $cl 2>&1 | Select-Object -First 1
    $linkIdentity = & $link 2>&1 | Select-Object -First 1
    $libIdentity = & $lib 2>&1 | Select-Object -First 1
    $nmakeIdentity = & $nmake /? 2>&1 | Select-Object -First 1
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
Write-Host $clIdentity
Write-Host $linkIdentity
Write-Host $libIdentity
Write-Host $nmakeIdentity
Write-Host (& $nasm -v)
Write-Host (& $bash --version | Select-Object -First 1)
Write-Host (& $cmake --version | Select-Object -First 1)
& $python -c 'import sys; assert sys.version_info >= (3, 12); print(sys.executable, sys.version)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ identity check failed.' }

New-Item -ItemType Directory -Force -Path $physicalOutput | Out-Null
$physicalSource = Join-Path $physicalOutput 'source-proof/source/ffmpeg-9.0.1'
$physicalInstall = Join-Path $physicalOutput 'install'
$physicalBuild = Join-Path $physicalOutput 'build'
$physicalStage = Join-Path $physicalOutput 'stage'

Invoke-LarixStableBuildRoot -PhysicalRoot $physicalOutput -Action {
param($stableRoot)
$output = $stableRoot
& $python -m scripts.common.source --repo-root $repo --output (Join-Path $output 'source-proof')
if ($LASTEXITCODE -ne 0) { throw 'Verified source preparation failed.' }
$source = Join-Path $output 'source-proof/source/ffmpeg-9.0.1'
$install = Join-Path $output 'install'
$build = Join-Path $output 'build'
$stage = Join-Path $output 'stage'
$package = Join-Path $output 'package'
foreach ($path in @($install, $build, $stage, $package)) {
    if (Test-Path -LiteralPath $path) { throw ('Output path already exists: ' + $path) }
    New-Item -ItemType Directory -Path $path | Out-Null
}
$configureJson = & $python -c 'import json,pathlib,sys;from scripts.common.model import compose_configure_args;print(json.dumps(compose_configure_args(pathlib.Path(sys.argv[1]),sys.argv[2],sys.argv[3])))' $repo $Profile 'windows-x64-msvc'
if ($LASTEXITCODE -ne 0) { throw 'Configure argument composition failed.' }
[string[]]$configure = $configureJson | ConvertFrom-Json
$configure += @(
    '--toolchain=msvc', '--arch=x86_64', '--target-os=win64',
    '--cc=larix-msvc-cl.cmd')
$deterministicCFlags = @(
    '/experimental:deterministic', '/Brepro',
    '/pathmap:./src=larix-source', '/pathmap:.=larix-build',
    '/pathmap:../install=larix-install', '/pathmap:..=larix-output')
$configure += ('--extra-cflags=' + ($deterministicCFlags -join ' '))
$configure += '--extra-ldflags=/Brepro /PDBALTPATH:%_PDB%'
$configure += '--prefix=../install'
$buildMsys = Msys-Path $build
$sourceJunction = Join-Path $build 'src'
New-Item -ItemType Junction -Path $sourceJunction -Target $source | Out-Null
$absolutePathMaps = @(
    ('/pathmap:' + $sourceJunction + '=larix-source'),
    ('/pathmap:' + $source + '=larix-source'),
    ('/pathmap:' + $build + '=larix-build'),
    ('/pathmap:' + $install + '=larix-install'),
    ('/pathmap:' + $output + '=larix-output')
)
$absoluteCompilerFlags = @(
    '/experimental:deterministic',
    '/Brepro'
) + $absolutePathMaps
$quotedAbsoluteCompilerFlags = @(
    $absoluteCompilerFlags | ForEach-Object { [char]34 + $_ + [char]34 }
)
$quotedArgs = @($configure) | ForEach-Object { Bash-Quote $_ }
$jobs = [Math]::Max(1, [Environment]::ProcessorCount)
$toolDirectories = @(
    (Split-Path $bash),
    (Split-Path $make),
    (Split-Path $nasm),
    (Split-Path $cmp),
    (Split-Path $msvcClProxy)
) | Select-Object -Unique
$toolPathPrefix = @(
    $toolDirectories | ForEach-Object { Bash-Quote (Msys-Path $_) }
) -join ':'
$shellCommand = 'set -euo pipefail; export MSYS2_ARG_CONV_EXCL=''/Brepro;/PDBALTPATH:;/pathmap:;/experimental:;./src''; export PATH={0}:$PATH; cd {1}; {2} {3}; {4} -j{5}; {4} install' -f `
    $toolPathPrefix, (Bash-Quote $buildMsys), `
    (Bash-Quote 'src/configure'), ($quotedArgs -join ' '), `
    (Bash-Quote (Msys-Path $make)), $jobs
$env:MSYS2_ARG_CONV_EXCL = '*'
$buildExitCode = -1
try {
    $env:CL = ($quotedAbsoluteCompilerFlags -join ' ')
    & $bash --noprofile --norc -lc $shellCommand
    $buildExitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:CL -ErrorAction SilentlyContinue
}
if ($buildExitCode -ne 0) { throw 'FFmpeg MSVC build failed.' }

foreach ($directory in @('bin', 'include', 'lib', 'symbols', 'lib/cmake/LarixFFmpegSDK', 'share/larix-ffmpeg-sdk')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $stage $directory) | Out-Null
}
$runtimeJson = & $python -c 'import json; from scripts.common.release_manifest import WINDOWS_RUNTIME_FILES; print(json.dumps(WINDOWS_RUNTIME_FILES))'
if ($LASTEXITCODE -ne 0) { throw 'Runtime inventory resolution failed.' }
[string[]]$runtimePaths = $runtimeJson | ConvertFrom-Json
$runtime = @($runtimePaths | ForEach-Object { [IO.Path]::GetFileName($_) })
$pdbSources = @{ 'ffprobe.exe' = 'ffprobe_g.pdb' }
foreach ($name in $runtime) {
    Copy-Item -LiteralPath (Join-Path $install ('bin/' + $name)) -Destination (Join-Path $stage 'bin')
    $pdbName = [IO.Path]::GetFileNameWithoutExtension($name) + '.pdb'
    $pdbSource = if ($pdbSources.ContainsKey($name)) { $pdbSources[$name] } else { $pdbName }
    $pdb = Get-ChildItem -LiteralPath @($build, $install) -Recurse -File -Filter $pdbSource | Select-Object -First 1
    if (-not $pdb) { throw ('Required PDB was not produced: ' + $pdbName) }
    Copy-Item -LiteralPath $pdb.FullName -Destination (Join-Path $stage ('symbols/' + $pdbName))
}
foreach ($component in @('avutil', 'avcodec', 'avformat', 'swresample', 'swscale')) {
    Copy-Item -LiteralPath (Join-Path $install ('include/lib' + $component)) -Destination (Join-Path $stage 'include') -Recurse
    Copy-Item -LiteralPath (Join-Path $install ('bin/' + $component + '.lib')) -Destination (Join-Path $stage 'lib')
}
Copy-Item -LiteralPath (Join-Path $repo 'cmake/LarixFFmpegSDKConfig.cmake.in') -Destination (Join-Path $stage 'lib/cmake/LarixFFmpegSDK/LarixFFmpegSDKConfig.cmake')
& $python -m scripts.common.stage_sdk --source-root $source --repo-root $repo --stage-root $stage --profile $Profile
if ($LASTEXITCODE -ne 0) { throw 'Legal and build provenance staging failed.' }
$lock = Get-Content -LiteralPath (Join-Path $repo 'config/ffmpeg.lock.json') -Raw | ConvertFrom-Json
$provenance = [ordered]@{ archive = $lock.source.archive; sha256 = $lock.source.sha256; size = $lock.source.size; url = $lock.source.url }
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $stage 'share/larix-ffmpeg-sdk/source.json'), (($provenance | ConvertTo-Json) + "`n"), $utf8)
& (Join-Path $PSScriptRoot 'inspect.ps1') -SdkRoot $stage -ExpectedPeCsv ($runtimePaths -join ';') -ReportPath (Join-Path $output 'inspection.json')
if ($LASTEXITCODE -ne 0) { throw 'Windows binary inspection failed.' }
$inspection = Get-Content -LiteralPath (Join-Path $output 'inspection.json') -Raw | ConvertFrom-Json
$buildInfo = [ordered]@{
    forbiddenPaths = @(
        [IO.Path]::GetFullPath($physicalSource),
        [IO.Path]::GetFullPath($physicalBuild),
        [IO.Path]::GetFullPath($physicalInstall),
        [IO.Path]::GetFullPath($physicalStage),
        [IO.Path]::GetFullPath($physicalOutput)
    )
    runtimeDependencies = $inspection.runtimeDependencies
    toolchain = $inspection.toolchain
}
[IO.File]::WriteAllText(
    (Join-Path $output 'build-info.json'),
    (($buildInfo | ConvertTo-Json -Depth 10) + "`n"),
    $utf8
)
& $python -m scripts.common.release_manifest --repo-root $repo --sdk-root $stage --profile $Profile --target windows-x64-msvc --build-info (Join-Path $output 'build-info.json')
if ($LASTEXITCODE -ne 0) { throw 'Release metadata generation failed.' }
$asset = & $python -c 'import pathlib,sys;from scripts.common.model import load_lock,load_target,target_asset_name;print(target_asset_name(load_lock(pathlib.Path(sys.argv[1])),sys.argv[3],load_target(pathlib.Path(sys.argv[2]))))' (Join-Path $repo 'config/ffmpeg.lock.json') (Join-Path $repo 'config/targets/windows-x64-msvc.json') $Profile
if ($LASTEXITCODE -ne 0) { throw 'Asset name resolution failed.' }
$archive = Join-Path $package $asset.Trim()
& $python -m scripts.common.package --sdk-root $stage --archive $archive
if ($LASTEXITCODE -ne 0) { throw 'SDK packaging failed.' }
& $python -m scripts.common.verify_sdk --repo-root $repo --archive $archive
if ($LASTEXITCODE -ne 0) { throw 'SDK relocation verification failed.' }
Write-Host ('Created verified SDK: ' + $archive)
}
