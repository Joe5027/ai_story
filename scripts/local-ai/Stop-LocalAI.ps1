[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$RuntimeRoot,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root
$pidFile = Join-Path $layout.Run 'runtime-agent.json'
$expectedPython = [IO.Path]::GetFullPath((Join-Path $layout.Components 'runtime-agent\.venv\Scripts\python.exe'))

if ($DryRun -or $WhatIfPreference) {
    Write-AIStoryPlan -Action '仅停止 PID 文件记录且可验证属于本 Runtime 的 Agent 进程' -Target $pidFile
    return
}
if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host '没有 Runtime Agent PID 记录；未停止任何进程。' -ForegroundColor Yellow
    return
}

$metadata = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$metadata.executable) -ne $expectedPython) {
    throw "PID 记录中的可执行文件不属于当前 Runtime，拒绝停止: $($metadata.executable)"
}
$process = Get-Process -Id ([int]$metadata.pid) -ErrorAction SilentlyContinue
if (-not $process) {
    $staleName = "runtime-agent.stale.$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
    Move-Item -LiteralPath $pidFile -Destination (Join-Path $layout.Run $staleName)
    Write-Host '进程已不存在，PID 记录已保留为 stale 诊断文件。' -ForegroundColor Yellow
    return
}

$actualPath = $null
try { $actualPath = [IO.Path]::GetFullPath($process.Path) } catch { }
if (-not $actualPath -or $actualPath -ne $expectedPython) {
    throw "PID=$($metadata.pid) 当前进程路径无法确认为本 Runtime Python，拒绝停止。"
}
if ($PSCmdlet.ShouldProcess("PID=$($metadata.pid)", '停止 Runtime Agent')) {
    Stop-Process -Id ([int]$metadata.pid) -ErrorAction Stop
    $stoppedName = "runtime-agent.stopped.$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
    Move-Item -LiteralPath $pidFile -Destination (Join-Path $layout.Run $stoppedName)
    Write-Host 'Runtime Agent 已停止。Ollama、ComfyUI 和 LightX2V 进程未被本脚本宽泛终止。' -ForegroundColor Green
}
