[CmdletBinding()]
param(
    [ValidateSet('lgpl', 'gpl')][string]$Profile = 'lgpl',
    [ValidateSet('Release')][string]$Configuration = 'Release',
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$effectiveOutput = if ($OutputRoot) { $OutputRoot } else { 'build/windows-' + $Profile }
$resolvedOutput = if ([IO.Path]::IsPathRooted($effectiveOutput)) {
    [IO.Path]::GetFullPath($effectiveOutput)
} else {
    [IO.Path]::GetFullPath((Join-Path $repositoryRoot $effectiveOutput))
}
& (Join-Path $PSScriptRoot 'platforms/windows/build.ps1') `
    -RepoRoot $repositoryRoot -Profile $Profile -Configuration $Configuration `
    -OutputRoot $resolvedOutput
