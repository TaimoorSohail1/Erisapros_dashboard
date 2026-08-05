$ErrorActionPreference = "Stop"

$port = 27017
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dbPath = Join-Path $root ".mongodb-data"
$logDir = Join-Path $root "logs"
$logPath = Join-Path $logDir "mongod-dev-local.log"

function Test-MongoPort {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(1000, $false)
        if ($connected) {
            $client.EndConnect($async)
        }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

if (Test-MongoPort -Port $port) {
    Write-Host "MongoDB is already running on 127.0.0.1:$port"
    exit 0
}

$mongod = $env:MONGOD_PATH
if (-not $mongod -or -not (Test-Path $mongod)) {
    $mongod = Get-ChildItem "C:\Program Files\MongoDB\Server" -Recurse -Filter "mongod.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not $mongod -or -not (Test-Path $mongod)) {
    Write-Error "mongod.exe was not found. Install MongoDB Community Server or set MONGOD_PATH."
    exit 1
}

New-Item -ItemType Directory -Force -Path $dbPath | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Host "Starting MongoDB on 127.0.0.1:$port"
Write-Host "Data: $dbPath"
Write-Host "Log:  $logPath"

$args = @(
    "--dbpath", $dbPath,
    "--logpath", $logPath,
    "--bind_ip", "127.0.0.1",
    "--port", "$port",
    "--wiredTigerCacheSizeGB", "0.25"
)

Start-Process -FilePath $mongod -ArgumentList $args -WindowStyle Hidden

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if (Test-MongoPort -Port $port) {
        Write-Host "MongoDB started."
        exit 0
    }
}

Write-Error "MongoDB did not start on 127.0.0.1:$port."
if (Test-Path $logPath) {
    Get-Content $logPath -Tail 40
}
exit 1
