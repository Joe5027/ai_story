[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$RuntimeRoot,
    [switch]$DryRun,
    [switch]$WaitUntilReady,
    [ValidateRange(1, 300)][int]$ReadyTimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root
$python = Join-Path $layout.Components 'runtime-agent\.venv\Scripts\python.exe'
$envFile = Join-Path $layout.Config 'runtime-agent.env'
$pidFile = Join-Path $layout.Run 'runtime-agent.json'

if ($DryRun -or $WhatIfPreference) {
    Write-AIStoryPlan -Action '启动 Runtime Agent（隐藏窗口，127.0.0.1:9100）' -Target $python
    Write-Host 'Ollama、ComfyUI 不会由本脚本启动，请按固定版本运维说明单独启动；Agent 只连接服务或按任务监督 LightX2V/FFmpeg CLI。' -ForegroundColor Yellow
    return
}
foreach ($required in @($python, $envFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "缺少启动所需文件: $required"
    }
}
New-Item -ItemType Directory -Path $layout.Run, $layout.Logs -Force | Out-Null

if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
    $existing = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $process = Get-Process -Id ([int]$existing.pid) -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "Runtime Agent 已在运行，PID=$($existing.pid)" -ForegroundColor Yellow
        return
    }
    $staleName = "runtime-agent.stale.$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
    Move-Item -LiteralPath $pidFile -Destination (Join-Path $layout.Run $staleName)
}

$environment = Read-AIStoryEnvFile -Path $envFile
$oldValues = @{}
try {
    foreach ($entry in $environment.GetEnumerator()) {
        $oldValues[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
        [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value, 'Process')
    }
    $logStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdout = Join-Path $layout.Logs "runtime-agent.$logStamp.stdout.log"
    $stderr = Join-Path $layout.Logs "runtime-agent.$logStamp.stderr.log"
    if ($PSCmdlet.ShouldProcess($python, '启动 Runtime Agent')) {
        $process = Start-Process -FilePath $python `
            -ArgumentList @('-m', 'runtime_agent') `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -PassThru
        $metadata = [ordered]@{
            schema_version = 1
            pid = $process.Id
            executable = $python
            started_at = [DateTimeOffset]::Now.ToString('o')
            listen_url = 'http://127.0.0.1:9100'
        } | ConvertTo-Json
        [IO.File]::WriteAllText($pidFile, $metadata + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        Write-Host "Runtime Agent 已启动，PID=$($process.Id)" -ForegroundColor Green
    }
}
finally {
    foreach ($name in $oldValues.Keys) {
        [Environment]::SetEnvironmentVariable($name, $oldValues[$name], 'Process')
    }
}

if ($WaitUntilReady) {
    $headers = @{}
    $bearerToken = [string]$environment['RUNTIME_AGENT_BEARER_TOKEN']
    if (-not [string]::IsNullOrWhiteSpace($bearerToken)) {
        $headers.Authorization = "Bearer $bearerToken"
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    do {
        try {
            $live = Invoke-RestMethod -Uri 'http://127.0.0.1:9100/v1/health/live' -Method Get -TimeoutSec 2
            $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:9100/v1/health/ready' -Method Get -Headers $headers -TimeoutSec 2
            $capabilities = Invoke-RestMethod -Uri 'http://127.0.0.1:9100/v1/capabilities' -Method Get -Headers $headers -TimeoutSec 2
            if ($live.status -eq 'live' -and $ready.status -eq 'ready' -and $null -ne $capabilities) {
                Write-Host 'Runtime Agent live、ready 和 capabilities 检查通过。' -ForegroundColor Green
                return
            }
        }
        catch { Start-Sleep -Milliseconds 500 }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Runtime Agent 在 $ReadyTimeoutSeconds 秒内未通过 live 检查，请查看 $($layout.Logs)"
}
