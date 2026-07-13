[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$RuntimeRoot,
    [string]$DestinationRoot,
    [ValidateRange(100, 20000)][int]$TailLines = 5000,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

function Protect-SensitiveText {
    param([string]$Text)
    if ($null -eq $Text) { return '' }
    $protected = $Text -replace '(?i)(authorization\s*[:=]\s*bearer\s+)[^\s"'']+', '$1<redacted>'
    $protected = $protected -replace '(?im)^([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*=).+$', '$1<redacted>'
    $protected = $protected -replace '(?i)("(?:api_key|token|secret|password)"\s*:\s*")[^"]+', '$1<redacted>'
    $protected = $protected -replace '(?i)("(?:prompt|negative_prompt|input_artifacts)"\s*:\s*)("[^"]*"|\[[^\]]*\])', '$1"<redacted>"'
    return $protected
}

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root
if ([string]::IsNullOrWhiteSpace($DestinationRoot)) { $DestinationRoot = Join-Path $layout.Backups 'support' }
$destination = [IO.Path]::GetFullPath($DestinationRoot)
$logsPrefix = [IO.Path]::GetFullPath($layout.Logs).TrimEnd('\') + '\'
if (($destination.TrimEnd('\') + '\').StartsWith($logsPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "日志包目标不能位于日志来源内部: $destination"
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$bundle = Join-Path $destination "diagnostics-$stamp"

if ($DryRun -or $WhatIfPreference) {
    Write-AIStoryPlan -Action "收集脱敏日志末尾 $TailLines 行及只读系统诊断" -Target $bundle
    return
}
if (-not $PSCmdlet.ShouldProcess($bundle, '创建脱敏日志包目录')) { return }
New-Item -ItemType Directory -Path $bundle -Force | Out-Null

if (Test-Path -LiteralPath $layout.Logs -PathType Container) {
    $logTarget = Join-Path $bundle 'logs'
    New-Item -ItemType Directory -Path $logTarget -Force | Out-Null
    foreach ($log in Get-ChildItem -LiteralPath $layout.Logs -File | Where-Object Extension -in @('.log', '.txt', '.json', '.jsonl')) {
        $text = (Get-Content -LiteralPath $log.FullName -Tail $TailLines -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        $safeText = Protect-SensitiveText -Text $text
        [IO.File]::WriteAllText((Join-Path $logTarget $log.Name), $safeText + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    }
}

$diagnostics = [ordered]@{
    collected_at = [DateTimeOffset]::Now.ToString('o')
    os = [Environment]::OSVersion.VersionString
    powershell = $PSVersionTable.PSVersion.ToString()
    runtime_root = $root
    logical_disks = @(Get-CimInstance Win32_LogicalDisk -ErrorAction SilentlyContinue | Select-Object DeviceID, Size, FreeSpace)
    relevant_processes = @(Get-Process -ErrorAction SilentlyContinue | Where-Object ProcessName -match 'python|ollama|comfy|ffmpeg' | Select-Object Id, ProcessName)
    listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in @(9100, 11434, 8188) | Select-Object LocalAddress, LocalPort, OwningProcess)
}
$nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidia) {
    try { $diagnostics['nvidia_smi'] = (& $nvidia.Source --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader 2>&1 | Out-String).Trim() }
    catch { $diagnostics['nvidia_smi'] = $_.Exception.Message }
}
$json = Protect-SensitiveText -Text ($diagnostics | ConvertTo-Json -Depth 6)
[IO.File]::WriteAllText((Join-Path $bundle 'diagnostics.json'), $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

$readme = @'
该目录由 AI Story Collect-Logs.ps1 生成。
日志只保留末尾片段并执行了常见密钥模式脱敏，但发送给第三方前仍必须人工复核。
本包不包含 Runtime Agent 配置、数据库、模型、提示词原文或媒体文件。
'@
[IO.File]::WriteAllText((Join-Path $bundle 'README.txt'), $readme, [Text.UTF8Encoding]::new($false))
Write-Host "脱敏日志包已生成: $bundle" -ForegroundColor Green
