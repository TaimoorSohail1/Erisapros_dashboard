param(
    [string]$TaskName = "ERISAPros FT Williams Agent"
)

$ErrorActionPreference = "Stop"
$secureRoot = Join-Path $env:LOCALAPPDATA "ERISAPros\FTWLocalAgent"
$agentPath = Join-Path $secureRoot "ERISAProsFTWAgent.exe"
$credentialFile = Join-Path $secureRoot "device.credential"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if ((Test-Path -LiteralPath $agentPath -PathType Leaf) -and (Test-Path -LiteralPath $credentialFile -PathType Leaf)) {
    & $agentPath unpair --credential-file $credentialFile
    if ($LASTEXITCODE -ne 0) {
        throw "The computer could not be disconnected from ERISAPros. Retry while online before removing local files."
    }
}

$resolvedRoot = [System.IO.Path]::GetFullPath($secureRoot)
$expectedParent = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "ERISAPros"))
if (-not $resolvedRoot.StartsWith($expectedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove an unexpected directory."
}
if (Test-Path -LiteralPath $resolvedRoot) {
    Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
}

Write-Output "The ERISAPros FT Williams agent was disconnected and removed for this Windows user."
