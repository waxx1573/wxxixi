[CmdletBinding()]
param(
    [switch]$DiagnoseOnly,
    [switch]$NoOpenPanel
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$LauncherDir = $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'WxPiPi Launcher'
$ProjectRoot = Split-Path -Parent $LauncherDir
$StateDir = Join-Path $env:LOCALAPPDATA 'Akasha-WeChat'
$StatePath = Join-Path $StateDir 'launcher-state.json'
$LogPath = Join-Path $StateDir 'launcher.log'
$WeFlowStdoutPath = Join-Path $StateDir 'weflow.stdout.log'
$WeFlowStderrPath = Join-Path $StateDir 'weflow.stderr.log'
$BridgeStdoutPath = Join-Path $StateDir 'bridge.stdout.log'
$BridgeStderrPath = Join-Path $StateDir 'bridge.stderr.log'
$script:StartedBridge = $null
$MutexName = 'Global\WxPiPi-OneClickLauncher'

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Write-Step([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Read-State {
    if (Test-Path -LiteralPath $StatePath) {
        try { return Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json } catch { }
    }
    return [pscustomobject]@{}
}

function Save-State($State) {
    $State | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

function Get-RunningProcessPath([string[]]$Names) {
    foreach ($name in $Names) {
        $process = Get-Process -Name $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($process) {
            try {
                $path = $process.Path
                if ($path -and (Test-Path -LiteralPath $path)) { return $path }
            } catch { }
        }
    }
    return $null
}

function Find-Executable([string[]]$Names, [string]$CachedPath) {
    if ($CachedPath -and (Test-Path -LiteralPath $CachedPath -PathType Leaf)) { return $CachedPath }

    $running = Get-RunningProcessPath $Names
    if ($running) { return $running }

    foreach ($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths')) {
        foreach ($name in $Names) {
            $key = Join-Path $root ($name + '.exe')
            try {
                $value = (Get-ItemProperty -LiteralPath $key -ErrorAction Stop).'(default)'
                if ($value -and (Test-Path -LiteralPath $value -PathType Leaf)) { return $value }
            } catch { }
        }
    }

    foreach ($name in $Names) {
        $command = Get-Command ($name + '.exe') -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) { return $command.Source }
    }

    $startApps = Get-StartApps -ErrorAction SilentlyContinue | Where-Object { $_.Name -match ($Names -join '|') }
    foreach ($app in $startApps) {
        try {
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut((Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs" -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.BaseName -eq $app.Name } | Select-Object -First 1).FullName)
            if ($shortcut.TargetPath -and (Test-Path -LiteralPath $shortcut.TargetPath -PathType Leaf)) { return $shortcut.TargetPath }
        } catch { }
    }
    return $null
}

function Find-BridgeDirectory($State) {
    $cached = $State.bridge_directory
    if ($cached -and (Test-Path (Join-Path $cached 'main.py')) -and (Test-Path (Join-Path $cached 'config.example.json'))) { return $cached }

    $roots = @($ProjectRoot, (Join-Path $env:USERPROFILE 'Desktop'), (Join-Path $env:USERPROFILE 'Documents'), (Join-Path $env:USERPROFILE 'Downloads')) | Select-Object -Unique
    $candidates = foreach ($root in $roots) {
        if (Test-Path -LiteralPath $root) {
            Get-ChildItem -LiteralPath $root -Directory -Recurse -Force -ErrorAction SilentlyContinue |
                Where-Object { (Test-Path (Join-Path $_.FullName 'main.py')) -and (Test-Path (Join-Path $_.FullName 'config.example.json')) }
        }
    }
    $candidates = @($candidates | Select-Object -ExpandProperty FullName -Unique)
    if ($candidates.Count -eq 0) { return $null }
    if ($candidates.Count -gt 1) {
        $preferred = $candidates | Where-Object { Test-Path (Join-Path $_ 'config.json') } | Select-Object -First 1
        if ($preferred) { return $preferred }
        Write-Step "Multiple bridge projects found; using first candidate: $($candidates[0])"
    }
    return $candidates[0]
}

function Find-Python($State) {
    $candidates = @($State.python, (Get-Command py.exe -ErrorAction SilentlyContinue).Source, (Get-Command python.exe -ErrorAction SilentlyContinue).Source)
    foreach ($candidate in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        try {
            $pythonExe = $candidate
            if ([IO.Path]::GetFileName($candidate) -ieq 'py.exe') {
                $pythonExe = (& $candidate -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1).Trim()
            }
            if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { continue }
            $version = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ([version]$version -ge [version]'3.10') { return $pythonExe }
        } catch { }
    }
    return $null
}

function Wait-Tcp([string]$HostName, [int]$Port, [int]$TimeoutSeconds = 30) {
    $until = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ($Port -eq 8766 -and $script:StartedBridge -and $script:StartedBridge.HasExited) {
            throw "Bridge exited with code $($script:StartedBridge.ExitCode). See $BridgeStderrPath and $(Join-Path $bridgeDir 'bridge.log')."
        }
        try {
            $client = [Net.Sockets.TcpClient]::new()
            $task = $client.ConnectAsync($HostName, $Port)
            if ($task.Wait(700) -and $client.Connected) { $client.Dispose(); return $true }
            $client.Dispose()
        } catch { }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $until)
    return $false
}

function Test-WeChatLoggedIn([string]$Python) {
    $probe = @'
import sys
import uiautomation as auto

auto.SetGlobalSearchTimeout(1)
window = auto.WindowControl(searchDepth=3, Name='WeChat', searchInterval=0.2)
ready = window.Exists(1)
for automation_id in ('main_tabbar', 'main_window_main_splitter_view', 'main_window_sub_splitter_view'):
    ready = ready and window.Control(searchDepth=10, AutomationId=automation_id).Exists(1)
sys.exit(0 if ready else 1)
'@
    try {
        $probe | & $Python - 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Test-WeChatClientReady {
    $mainProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'Weixin.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine -notmatch '--type='
    })
    foreach ($main in $mainProcesses) {
        $children = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ParentProcessId -eq $main.ProcessId -and
            $_.Name -eq 'WeChatAppEx.exe' -and
            $_.CommandLine -and
            $_.CommandLine -notmatch '--type='
        })
        if ($children.Count -gt 0) { return $true }
    }

    $window = Get-Process -Name Weixin,WeChat -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match '微信|WeChat' } |
        Select-Object -First 1
    return [bool]$window
}

function Wait-WeChatWindow([string]$Python, [bool]$AllowMinimizedFallback, [int]$TimeoutSeconds = 60) {
    $until = (Get-Date).AddSeconds($TimeoutSeconds)
    $reportedFallback = $false
    do {
        if (Test-WeChatLoggedIn $Python) {
            Write-Step 'WeChat is fully logged in; main chat interface is ready.'
            return $true
        }
        if ($AllowMinimizedFallback -and (Test-WeChatClientReady) -and -not $reportedFallback) {
            Write-Step 'WeChat is already initialized but minimized; treating it as ready and continuing.'
            $reportedFallback = $true
            return $true
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $until)
    return $false
}

function Get-BridgePid([string]$BridgeDir) {
    $pidPath = Join-Path $BridgeDir 'bridge.pid'
    if (-not (Test-Path -LiteralPath $pidPath)) { return $null }
    try {
        $bridgeProcessId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
        $process = Get-Process -Id $bridgeProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -match '^python(w)?$') {
            $info = Get-CimInstance Win32_Process -Filter "ProcessId = $bridgeProcessId"
            $entry = Join-Path $BridgeDir 'main.py'
            if ($info.CommandLine -and $info.CommandLine.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $bridgeProcessId
            }
        }
    } catch { }
    return $null
}

function Wait-BridgeReady([int]$TimeoutSeconds = 75) {
    $until = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = $null
    do {
        try {
            $current = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/status' -TimeoutSec 5
            $summary = "running=$($current.running), weflow=$($current.weflow_connected), astrbot=$($current.ob_connected), uia=$($current.uia_ready)"
            if ($summary -ne $last) {
                Write-Step "Bridge readiness: $summary"
                $last = $summary
            }
            if ($current.running -and $current.weflow_connected -and $current.ob_connected -and $current.uia_ready) {
                return $current
            }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $until)
    return $null
}

function Start-BridgeProcess([string]$BridgeDir, [string]$Python) {
    $script:StartedBridge = Start-Process -FilePath $Python -ArgumentList @('-X', 'utf8', ('"{0}"' -f (Join-Path $BridgeDir 'main.py'))) -WorkingDirectory $BridgeDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $BridgeStdoutPath -RedirectStandardError $BridgeStderrPath
}

function Restart-Bridge([string]$BridgeDir, [string]$Python) {
    $bridgeProcessId = Get-BridgePid $BridgeDir
    if ($bridgeProcessId) {
        Write-Step "Stopping bridge process $bridgeProcessId for one readiness retry"
        Stop-Process -Id $bridgeProcessId -Force -ErrorAction SilentlyContinue
        $until = (Get-Date).AddSeconds(15)
        do {
            if (-not (Get-Process -Id $bridgeProcessId -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $until)
    }
    Start-Sleep -Seconds 2
    Start-BridgeProcess $BridgeDir $Python
}

function Start-DiscoveredApp([string]$Path, [string[]]$ProcessNames, [string]$StdoutPath = $null, [string]$StderrPath = $null) {
    if (Get-RunningProcessPath $ProcessNames) { return 'already running' }
    if (-not $Path) { return 'not found' }
    $params = @{ FilePath = $Path }
    if ($StdoutPath) { $params.RedirectStandardOutput = $StdoutPath }
    if ($StderrPath) { $params.RedirectStandardError = $StderrPath }
    Start-Process @params | Out-Null
    return 'started'
}

$mutex = [Threading.Mutex]::new($false, $MutexName)
if (-not $mutex.WaitOne(0)) { Write-Step 'Another launcher instance is already running.'; exit 0 }

try {
    $state = Read-State
    $wechat = Find-Executable @('Weixin', 'WeChat') $state.wechat
    $weflow = Find-Executable @('WeFlow') $state.weflow
    $bridgeDir = Find-BridgeDirectory $state
    $python = Find-Python $state

    if ($wechat) { $state | Add-Member NoteProperty wechat $wechat -Force }
    if ($weflow) { $state | Add-Member NoteProperty weflow $weflow -Force }
    if ($bridgeDir) { $state | Add-Member NoteProperty bridge_directory $bridgeDir -Force }
    if ($python) { $state | Add-Member NoteProperty python $python -Force }
    Save-State $state

    Write-Step "WeChat: $(if ($wechat) { $wechat } else { 'not found' })"
    Write-Step "WeFlow: $(if ($weflow) { $weflow } else { 'not found' })"
    Write-Step "Bridge directory: $(if ($bridgeDir) { $bridgeDir } else { 'not found' })"
    Write-Step "Python: $(if ($python) { $python } else { '3.10+ not found' })"
    if ($DiagnoseOnly) { exit 0 }

    if (-not $wechat) { throw 'WeChat was not found. Install WeChat first.' }
    if (-not $weflow) { throw 'WeFlow was not found. Install WeFlow first.' }
    if (-not $bridgeDir) { throw 'Bridge project was not found (main.py and config.example.json are required).' }
    if (-not $python) { throw 'Python 3.10 or newer was not found.' }

    $wechatAction = Start-DiscoveredApp $wechat @('Weixin', 'WeChat')
    Write-Step "WeChat: $wechatAction"
    $allowMinimizedFallback = ($wechatAction -eq 'already running')
    if (-not (Wait-WeChatWindow $python $allowMinimizedFallback 120)) { throw 'WeChat did not reach the logged-in main interface within 120 seconds.' }
    $weflowAction = Start-DiscoveredApp $weflow @('WeFlow') $WeFlowStdoutPath $WeFlowStderrPath
    Write-Step "WeFlow: $weflowAction"
    if (-not (Wait-Tcp '127.0.0.1' 5031 60)) { throw 'WeFlow API did not listen on port 5031 within 60 seconds.' }
    Write-Step 'WeFlow API: connected'

    $bridgePid = Get-BridgePid $bridgeDir
    $bridgeRunning = [bool]$bridgePid
    if (-not $bridgeRunning) {
        Start-BridgeProcess $bridgeDir $python
        Write-Step 'Bridge: started'
    } else { Write-Step 'Bridge: already running' }

    if (-not (Wait-Tcp '127.0.0.1' 8766 45)) { throw 'Bridge panel did not listen on port 8766 within 45 seconds.' }
    $status = Wait-BridgeReady 20
    if (-not $status) {
        Write-Step 'Bridge was not fully ready; retrying one clean bridge start.'
        Restart-Bridge $bridgeDir $python
        if (-not (Wait-Tcp '127.0.0.1' 8766 45)) { throw 'Bridge panel did not listen after the readiness retry.' }
        $status = Wait-BridgeReady 75
    }
    if (-not $status) { throw 'Bridge did not become fully ready: running, WeFlow, AstrBot and UIA must all be true.' }
    Write-Step ("Bridge status: running={0}, weflow={1}, astrbot={2}, uia={3}" -f $status.running, $status.weflow_connected, $status.ob_connected, $status.uia_ready)
    Write-Host "`nStartup complete. All required connections are ready."
    Write-Host "Closing this startup window in 5 seconds. Log: $LogPath"
    Start-Sleep -Seconds 5
    exit 0
} catch {
    Write-Step "Startup failed: $($_.Exception.Message)"
    exit 1
} finally {
    $mutex.ReleaseMutex(); $mutex.Dispose()
}
