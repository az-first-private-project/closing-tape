# ============================================================
# GFNA V5 · 本地一键启动（独立进程，不随会话结束退出）
#   用法:
#     .\start-local.ps1                 # 后端 8000 + 前端 5173
#     .\start-local.ps1 -Demo           # 空库时先灌演示数据
#     .\start-local.ps1 -Stop           # 停止
#     .\start-local.ps1 -BackendPort 8000 -FrontendPort 5173
#   日志: logs\backend.log / logs\frontend.log
# ============================================================
param(
    [switch]$Stop,
    [switch]$Demo,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logs = Join-Path $root 'logs'
$py = Join-Path $root '.venv\Scripts\python.exe'
$vite = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'

function Stop-Port([int]$port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop; "stopped PID $($c.OwningProcess) on :$port" }
        catch { "could not stop PID $($c.OwningProcess): $($_.Exception.Message)" }
    }
    if (-not $conns) { "nothing listening on :$port" }
}

if ($Stop) {
    Stop-Port $BackendPort
    Stop-Port $FrontendPort
    return
}

New-Item -ItemType Directory -Force -Path $logs | Out-Null

if (-not (Test-Path $py)) { throw "未找到虚拟环境: $py（先执行 backend 依赖安装）" }
if (-not (Test-Path $vite)) { throw "未找到 vite: $vite（先执行 cd frontend; npm install）" }

# 端口占用检查
foreach ($p in @($BackendPort, $FrontendPort)) {
    $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($busy) { throw "端口 $p 已被占用（PID $($busy[0].OwningProcess)）。先运行 .\start-local.ps1 -Stop" }
}

# 数据库结构（幂等）
Push-Location (Join-Path $root 'backend')
try { & $py scripts\bootstrap_db.py 2>&1 | Select-Object -Last 3 } finally { Pop-Location }

if ($Demo) {
    Push-Location (Join-Path $root 'backend')
    try { & $py scripts\first_run.py 2>&1 | Select-Object -Last 5 } finally { Pop-Location }
}

# 用 cmd /c start /B 脱离控制台：否则子进程会继承调用方的 stdout 管道，
# 在"输出被重定向/捕获"的场景下父脚本会一直等不到 EOF（表现为命令挂住）。
$beCmd = "start `"gfna-backend`" /B `"$py`" -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort >> `"$logs\backend.log`" 2>&1"
$feCmd = "start `"gfna-frontend`" /B node node_modules\vite\bin\vite.js preview --port $FrontendPort --host 127.0.0.1 >> `"$logs\frontend.log`" 2>&1"

Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $beCmd `
    -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $feCmd `
    -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden

Start-Sleep -Seconds 8

foreach ($p in @($BackendPort, $FrontendPort)) {
    $up = $false
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $p); $up = $true; $c.Close() } catch {}
    if (-not $up) { Write-Warning "端口 $p 尚未就绪，请查看 $logs 下的日志" }
}

""
"后端 -> http://127.0.0.1:$BackendPort/docs   (日志 logs\backend.log)"
"前端 -> http://127.0.0.1:$FrontendPort       (日志 logs\frontend.log)"
"停止: .\start-local.ps1 -Stop"
