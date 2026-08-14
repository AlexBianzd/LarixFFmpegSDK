function Invoke-LarixStableBuildRoot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PhysicalRoot,
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [string]$SubstCommand = 'subst.exe'
    )
    Set-StrictMode -Version Latest

    $driveName = 'R'
    $drive = 'R:'
    $stableRoot = 'R:\'
    $recovery = 'Run subst R: /d after confirming the mapping belongs to LarixFFmpegSDK.'
    if (Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue) {
        throw ('Stable build drive R: is already occupied. ' + $recovery)
    }
    $existingMappings = @(& subst.exe)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect existing subst mappings.' }
    if ($existingMappings -match '(?i)^R:\\: =>') {
        throw ('Stable build drive R: is already occupied. ' + $recovery)
    }

    $physical = [IO.Path]::GetFullPath($PhysicalRoot)
    if (-not (Test-Path -LiteralPath $physical -PathType Container)) {
        throw ('Stable build backing directory does not exist: ' + $physical)
    }
    $ownsMapping = $false
    try {
        & $SubstCommand $drive $physical
        if ($LASTEXITCODE -ne 0) {
            throw ('Stable build mapping failed to create. ' + $recovery)
        }
        $ownsMapping = $true
        $createdMappings = @(& subst.exe)
        if ($LASTEXITCODE -ne 0 -or $createdMappings -notmatch '(?i)^R:\\: =>') {
            throw ('Stable build mapping failed to create. ' + $recovery)
        }
        & $Action $stableRoot
    }
    finally {
        if ($ownsMapping) {
            & $SubstCommand $drive '/d'
            $cleanupExitCode = $LASTEXITCODE
            $remainingMappings = @(& subst.exe)
            $remainingExitCode = $LASTEXITCODE
            $stillMapped = $remainingMappings -match '(?i)^R:\\: =>'
            if ($cleanupExitCode -ne 0 -or $remainingExitCode -ne 0 -or $stillMapped) {
                throw ('Stable build mapping failed to remove. ' + $recovery)
            }
        }
    }
}
