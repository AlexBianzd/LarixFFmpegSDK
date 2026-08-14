[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SdkRoot,
    [Parameter(Mandatory = $true)][string]$ExpectedPeCsv,
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = [IO.Path]::GetFullPath($SdkRoot)
if (-not (Get-Command dumpbin.exe -ErrorAction SilentlyContinue)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
        throw 'vswhere.exe is required to discover Visual Studio 2022'
    }
    $installation = (& $vswhere -latest -products * -version '[17.0,18.0)' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
    if (-not $installation) { throw 'Visual Studio 2022 C++ tools were not found' }
    $vcvars = Join-Path $installation 'VC/Auxiliary/Build/vcvars64.bat'
    if (-not (Test-Path -LiteralPath $vcvars -PathType Leaf)) {
        throw 'vcvars64.bat is missing from Visual Studio 2022'
    }
    $environmentLines = & cmd.exe /d /s /c ('"' + $vcvars + '" >nul && set')
    if ($LASTEXITCODE -ne 0) { throw 'vcvars64.bat failed' }
    foreach ($line in $environmentLines) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) {
            [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1), 'Process')
        }
    }
}
$dumpbin = (Get-Command dumpbin.exe -ErrorAction Stop).Source
$expectedPe = @($ExpectedPeCsv.Split(';') | Where-Object { $_ } | Sort-Object)
if ($expectedPe.Count -ne 6 -or $expectedPe -notcontains 'bin/ffprobe.exe') {
    throw 'Expected PE inventory is invalid'
}
$actualPe = @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object {
    $_.Extension -in @('.dll', '.exe')
} | ForEach-Object {
    $_.FullName.Substring($root.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/')
} | Sort-Object)
if (Compare-Object $expectedPe $actualPe) {
    throw "Unexpected PE inventory: $($actualPe -join ', ')"
}
$packaged = @{}
foreach ($path in $expectedPe) { $packaged[[IO.Path]::GetFileName($path).ToLowerInvariant()] = $true }
$allowedSystem = @(
    'advapi32.dll', 'bcrypt.dll', 'crypt32.dll', 'gdi32.dll', 'kernel32.dll',
    'msvcp140.dll', 'ole32.dll', 'oleaut32.dll', 'secur32.dll', 'shell32.dll',
    'ucrtbase.dll', 'user32.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
    'winmm.dll', 'ws2_32.dll'
)
$resolvedDependencies = [ordered]@{}
foreach ($relative in $expectedPe) {
    $path = Join-Path $root $relative
    $headers = & $dumpbin /headers $path 2>&1
    if ($LASTEXITCODE -ne 0 -or -not ($headers -match 'machine \(x64\)')) {
        throw "PE is not x64 MSVC-compatible: $relative"
    }
    $dependencies = & $dumpbin /dependents $path 2>&1
    if ($LASTEXITCODE -ne 0) { throw "dumpbin failed for $relative" }
    $resolved = @()
    foreach ($match in [regex]::Matches(($dependencies -join "`n"), '(?im)^\s*([A-Za-z0-9_.-]+\.dll)\s*$')) {
        $dependency = $match.Groups[1].Value.ToLowerInvariant()
        $resolved += $dependency.ToUpperInvariant()
        if ($dependency -match '(msys|mingw|cygwin)') {
            throw "Forbidden POSIX runtime dependency in ${relative}: $dependency"
        }
        if (-not $packaged.ContainsKey($dependency) -and
            $dependency -notin $allowedSystem -and
            -not $dependency.StartsWith('api-ms-win-') -and
            -not $dependency.StartsWith('ext-ms-win-')) {
            throw "Unknown PE dependency in ${relative}: $dependency"
        }
    }
    $resolvedDependencies[$relative] = @($resolved | Sort-Object -Unique)
}
$expectedImportLibraries = @(
    'lib/avcodec.lib', 'lib/avformat.lib', 'lib/avutil.lib',
    'lib/swresample.lib', 'lib/swscale.lib'
)
$actualLibraries = @(Get-ChildItem -LiteralPath (Join-Path $root 'lib') -File -Filter '*.lib' | ForEach-Object {
    'lib/' + $_.Name
} | Sort-Object)
if (Compare-Object $expectedImportLibraries $actualLibraries) {
    throw ('Unexpected MSVC library inventory: ' + ($actualLibraries -join ', '))
}
foreach ($relative in $expectedImportLibraries) {
    $headers = & $dumpbin /headers (Join-Path $root $relative) 2>&1
    if ($LASTEXITCODE -ne 0 -or -not ($headers -match 'DLL name')) {
        throw ('Library is not an MSVC DLL import library: ' + $relative)
    }
}
$pdbFiles = @(Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.pdb')
$expectedPdb = @($expectedPe | ForEach-Object { 'symbols/' + [IO.Path]::GetFileNameWithoutExtension($_) + '.pdb' } | Sort-Object)
$actualPdb = @($pdbFiles | ForEach-Object { $_.FullName.Substring($root.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/') } | Sort-Object)
if (Compare-Object $expectedPdb $actualPdb) { throw ('Unexpected PDB inventory: ' + ($actualPdb -join ', ')) }
foreach ($pdb in $pdbFiles) {
    $relative = $pdb.FullName.Substring($root.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/')
    if (-not $relative.StartsWith('symbols/')) { throw "PDB is outside symbols/: $relative" }
}
if ($ReportPath) {
    $report = [ordered]@{
        runtimeDependencies = $resolvedDependencies
        toolchain = [ordered]@{
            compiler = 'MSVC ' + $env:VCToolsVersion.TrimEnd('\')
            windowsSdk = $env:WindowsSDKVersion.TrimEnd('\')
        }
    }
    $json = $report | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($ReportPath), $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}
Write-Host "Verified $($expectedPe.Count) Windows x64 PE files and $($pdbFiles.Count) PDB files."
