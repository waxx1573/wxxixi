$ErrorActionPreference = 'Stop'

$bridgeDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\wechat-weflow-bridge-ob11')).Path
$logPath = Join-Path $PSScriptRoot 'wechat-bridge-watchdog.log'
$stateDir = Join-Path $env:LOCALAPPDATA 'Akasha-WeChat'
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$python = (Get-Command python.exe -ErrorAction Stop).Source

while ($true) {
    $entry = Join-Path $bridgeDir 'main.py'
    $existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -ge 0
    }
    if ($existing) {
        Start-Sleep -Seconds 15
        continue
    }
    $started = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $logPath -Value "$started starting bridge"

    try {
        $process = Start-Process -FilePath $python -ArgumentList @('-X', 'utf8', ('"{0}"' -f (Join-Path $bridgeDir 'main.py'))) -WorkingDirectory $bridgeDir -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput (Join-Path $stateDir 'watchdog-bridge.stdout.log') -RedirectStandardError (Join-Path $stateDir 'watchdog-bridge.stderr.log')
        Add-Content -LiteralPath $logPath -Value "Bridge process exited with code $($process.ExitCode)"
    } catch {
        Add-Content -LiteralPath $logPath -Value $_.Exception.Message
    }

    $stopped = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $logPath -Value "$stopped bridge exited; retrying in 15 seconds"
    Start-Sleep -Seconds 15
}
