param(
    [string]$SigningCertificateThumbprint = "",
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendRoot = Join-Path $repositoryRoot "backend"
$entryPoint = Join-Path $backendRoot "scripts\run_ftw_local_agent.py"
$outputDirectory = Join-Path $repositoryRoot "output\ftw-local-agent"

if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "FT Williams local-agent entry point was not found."
}

Push-Location $backendRoot
try {
    python -m pip install -r agent-requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Agent build dependencies could not be installed." }

    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "The dedicated Chromium runtime could not be installed." }

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --name ERISAProsFTWAgent `
        --distpath $outputDirectory `
        --workpath (Join-Path $outputDirectory "build") `
        --specpath (Join-Path $outputDirectory "spec") `
        --collect-all playwright `
        $entryPoint
    if ($LASTEXITCODE -ne 0) { throw "The local-agent executable build failed." }
} finally {
    Pop-Location
}

$executable = Join-Path $outputDirectory "ERISAProsFTWAgent.exe"
if ($SigningCertificateThumbprint) {
    $signTool = Get-Command signtool.exe -ErrorAction Stop
    & $signTool.Source sign /sha1 $SigningCertificateThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $executable
    if ($LASTEXITCODE -ne 0) { throw "The local-agent executable could not be signed." }
    & $signTool.Source verify /pa $executable
    if ($LASTEXITCODE -ne 0) { throw "The local-agent signature could not be verified." }
} elseif ($RequireSignature) {
    throw "A signing certificate thumbprint is required for a production build."
} else {
    Write-Warning "Created an unsigned development build. Production distribution requires -RequireSignature and a signing certificate."
}

$hash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash
$signature = Get-AuthenticodeSignature -LiteralPath $executable
$manifest = [ordered]@{
    product = "ERISAPros FT Williams Agent"
    executable = [System.IO.Path]::GetFileName($executable)
    sha256 = $hash
    signature_status = [string]$signature.Status
    built_at_utc = [DateTime]::UtcNow.ToString("o")
}
$manifestPath = Join-Path $outputDirectory "release-manifest.json"
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Output $executable
Write-Output $manifestPath
