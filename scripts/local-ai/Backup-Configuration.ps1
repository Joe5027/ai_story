[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$RuntimeRoot,
    [string]$DestinationRoot,
    [switch]$IncludeSecrets,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root
if ([string]::IsNullOrWhiteSpace($DestinationRoot)) { $DestinationRoot = $layout.Backups }
$destination = [IO.Path]::GetFullPath($DestinationRoot)
foreach ($sourceDirectory in @($layout.Config, $layout.Workflows)) {
    $sourcePrefix = [IO.Path]::GetFullPath($sourceDirectory).TrimEnd('\') + '\'
    if (($destination.TrimEnd('\') + '\').StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "备份目标不能位于备份来源内部: $destination"
    }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupRoot = Join-Path $destination "config-$stamp"

Write-Host "备份来源: $root" -ForegroundColor Cyan
Write-Host "备份目标: $backupRoot" -ForegroundColor Cyan
Write-Host '模型、缓存、Agent journal、媒体和临时产物不会进入配置备份。' -ForegroundColor Yellow
if (-not $IncludeSecrets) {
    Write-Host 'Runtime Agent 令牌默认脱敏；恢复后需重新配置令牌。' -ForegroundColor Yellow
}
if ($DryRun -or $WhatIfPreference) {
    Write-AIStoryPlan -Action '备份 config、workflows 和安装记录（不覆盖已有备份）' -Target $backupRoot
    return
}
if (-not $PSCmdlet.ShouldProcess($backupRoot, '创建配置备份目录')) { return }
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

foreach ($sourceDirectory in @($layout.Config, $layout.Workflows)) {
    if (-not (Test-Path -LiteralPath $sourceDirectory -PathType Container)) { continue }
    $folderName = Split-Path $sourceDirectory -Leaf
    $targetDirectory = Join-Path $backupRoot $folderName
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $sourceDirectory -File -Recurse) {
        $relative = [IO.Path]::GetRelativePath($sourceDirectory, $file.FullName)
        $target = Join-Path $targetDirectory $relative
        New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
        if (-not $IncludeSecrets -and $file.Extension.ToLowerInvariant() -in @('.env', '.json', '.toml', '.yaml', '.yml', '.txt', '.ini', '.cfg')) {
            $redacted = Get-Content -LiteralPath $file.FullName -Raw
            $redacted = $redacted -replace '(?im)^([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*=).+$', '$1<redacted>'
            $redacted = $redacted -replace '(?i)("(?:api_key|token|secret|password)"\s*:\s*")[^"]+', '$1<redacted>'
            $redacted = $redacted -replace '(?im)^((?:api_key|token|secret|password)\s*=\s*)[^#\r\n]+', '$1"<redacted>"'
            [IO.File]::WriteAllText($target, $redacted, [Text.UTF8Encoding]::new($false))
        }
        else {
            Copy-Item -LiteralPath $file.FullName -Destination $target
        }
    }
}

foreach ($manifestName in @('manifest.example.json', 'components.example.json')) {
    $scriptManifest = Join-Path $PSScriptRoot $manifestName
    if (Test-Path -LiteralPath $scriptManifest -PathType Leaf) {
        Copy-Item -LiteralPath $scriptManifest -Destination (Join-Path $backupRoot $manifestName)
    }
}
$metadata = [ordered]@{
    schema_version = 1
    created_at = [DateTimeOffset]::Now.ToString('o')
    source_root = $root
    includes_secrets = [bool]$IncludeSecrets
    excludes = @('models', 'cache', 'data', 'staging', 'artifacts', 'media')
} | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $backupRoot 'backup-manifest.json'), $metadata + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
Write-Host "配置备份完成: $backupRoot" -ForegroundColor Green
