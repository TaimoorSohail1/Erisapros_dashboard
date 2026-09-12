param(
    [string]$AgentExecutable = "",
    [string]$ServerUrl = "https://d3axcdlq9aydpw.cloudfront.net",
    [string]$PairingCode = "",
    [string]$TaskName = "ERISAPros FT Williams Agent",
    [string]$ExpectedSha256 = "",
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"
$defaultAgentExecutable = Join-Path $PSScriptRoot "ERISAProsFTWAgentSetup.exe"
if (-not $AgentExecutable.Trim()) {
    $AgentExecutable = $defaultAgentExecutable
}
if (-not $PairingCode.Trim()) {
    $PairingCode = Read-Host "Enter the one-time connection code from ERISAPros"
}
if (-not $PairingCode.Trim()) {
    throw "A one-time ERISAPros connection code is required."
}
$agentPath = (Resolve-Path -LiteralPath $AgentExecutable).Path
if ([System.IO.Path]::GetExtension($agentPath) -ne ".exe") {
    throw "The local agent must be a Windows executable."
}
if ($ExpectedSha256) {
    $actualHash = (Get-FileHash -LiteralPath $agentPath -Algorithm SHA256).Hash
    if ($actualHash -ne $ExpectedSha256.Trim()) {
        throw "The local-agent checksum does not match the approved release."
    }
}
$signature = Get-AuthenticodeSignature -LiteralPath $agentPath
if ($RequireSignature -and $signature.Status -ne "Valid") {
    throw "The local agent must have a valid code signature before installation."
}

$secureRoot = Join-Path $env:LOCALAPPDATA "ERISAPros\FTWLocalAgent"
$profileDirectory = Join-Path $secureRoot "BrowserProfile"
$credentialFile = Join-Path $secureRoot "device.credential"
$installedAgent = Join-Path $secureRoot "ERISAProsFTWAgent.exe"
New-Item -ItemType Directory -Path $secureRoot -Force | Out-Null
New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null
Copy-Item -LiteralPath $agentPath -Destination $installedAgent -Force

& $installedAgent pair `
    --server-url $ServerUrl `
    --pairing-code $PairingCode `
    --device-name $env:COMPUTERNAME `
    --credential-file $credentialFile
if ($LASTEXITCODE -ne 0) { throw "This computer could not be paired with ERISAPros." }

$arguments = "run --credential-file `"$credentialFile`" --profile-dir `"$profileDirectory`""
$action = New-ScheduledTaskAction -Execute $installedAgent -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Output "The verified ERISAPros FT Williams agent is paired and starts automatically when this Windows user signs in."
Write-Output "Open ERISAPros and click Test connection to confirm this computer is ready."
