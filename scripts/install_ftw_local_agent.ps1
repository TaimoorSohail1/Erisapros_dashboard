param(
    [Parameter(Mandatory = $true)][string]$AgentExecutable,
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [Parameter(Mandatory = $true)][string]$PairingCode,
    [string]$TaskName = "ERISAPros FT Williams Agent"
)

$ErrorActionPreference = "Stop"
$agentPath = (Resolve-Path -LiteralPath $AgentExecutable).Path
if ([System.IO.Path]::GetExtension($agentPath) -ne ".exe") {
    throw "The local agent must be a Windows executable."
}

$secureRoot = Join-Path $env:LOCALAPPDATA "ERISAPros\FTWLocalAgent"
$profileDirectory = Join-Path $secureRoot "BrowserProfile"
$credentialFile = Join-Path $secureRoot "device.credential"
New-Item -ItemType Directory -Path $secureRoot -Force | Out-Null
New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null

& $agentPath pair `
    --server-url $ServerUrl `
    --pairing-code $PairingCode `
    --device-name $env:COMPUTERNAME `
    --credential-file $credentialFile
if ($LASTEXITCODE -ne 0) { throw "This computer could not be paired with ERISAPros." }

$arguments = "run --credential-file `"$credentialFile`" --profile-dir `"$profileDirectory`""
$action = New-ScheduledTaskAction -Execute $agentPath -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Output "The ERISAPros FT Williams agent is paired and starts automatically when this Windows user signs in."
