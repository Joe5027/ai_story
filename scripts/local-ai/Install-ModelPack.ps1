[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$RuntimeRoot,
    [string]$ManifestPath = (Join-Path $PSScriptRoot 'manifest.example.json'),
    [string[]]$ModelId,
    [switch]$RestartPartial,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root
$runtimeMarker = Join-Path $root '.ai-story-runtime-root'
$manifestFullPath = [IO.Path]::GetFullPath($ManifestPath)
if (-not (Test-Path -LiteralPath $manifestFullPath -PathType Leaf)) {
    throw "找不到模型清单: $manifestFullPath"
}
$manifest = Get-Content -LiteralPath $manifestFullPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or -not $manifest.packages) {
    throw '模型清单 schema_version 必须为 1，且必须包含 packages。'
}

function Write-ModelInstallRecord {
    param(
        [Parameter(Mandatory)][object]$Package,
        [Parameter(Mandatory)][string]$Identifier,
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][string]$ExpectedHash,
        [Parameter(Mandatory)][string]$Status
    )

    $sourceUri = [uri][string]$Package.url
    $recordPath = Join-Path $layout.Config "model-install.$Identifier.$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
    $record = [ordered]@{
        schema_version = 1
        status = $Status
        recorded_at = [DateTimeOffset]::Now.ToString('o')
        package_id = $Identifier
        component = [string]$Package.component
        version = [string]$Package.version
        license = [string]$Package.license
        license_url = [string]$Package.license_url
        size_bytes = [int64]$Package.size_bytes
        sha256 = $ExpectedHash
        target = $Target
        # 清单 URL 可能含短期签名查询参数；安装记录只保留不含 query/fragment 的来源路径。
        source_url = $sourceUri.GetLeftPart([UriPartial]::Path)
        source_manifest = $manifestFullPath
        source_manifest_sha256 = (Get-FileHash -LiteralPath $manifestFullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    } | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($recordPath, $record + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    return $recordPath
}

$packages = @($manifest.packages)
if (-not $ModelId -or $ModelId.Count -eq 0) {
    Write-Host '未指定 -ModelId：仅显示清单，不会下载任何内容。' -ForegroundColor Yellow
    $packages | Select-Object id, name, component, license, size_display, download_enabled | Format-Table -AutoSize
    return
}

$duplicateIds = @($packages | Group-Object id | Where-Object Count -gt 1)
if ($duplicateIds.Count -gt 0) { throw "模型清单含重复 ID: $($duplicateIds.Name -join ', ')" }

foreach ($requestedId in $ModelId) {
    if ($requestedId -notmatch '^[a-z0-9][a-z0-9._-]{1,79}$') {
        throw "模型 ID 格式无效: $requestedId"
    }
    $package = $packages | Where-Object id -eq $requestedId | Select-Object -First 1
    if (-not $package) { throw "模型清单不存在 ID: $requestedId" }

    $target = Resolve-AIStoryChildPath -Parent $layout.Models -RelativePath ([string]$package.target_relative_path)
    Write-Host ''
    Write-Host "模型包: $($package.name) [$($package.id)]" -ForegroundColor Green
    Write-Host "组件: $($package.component)"
    Write-Host "许可证: $($package.license)"
    Write-Host "许可证链接: $($package.license_url)"
    Write-Host "预计大小: $($package.size_display)"
    Write-Host "目标路径: $target"
    Write-Host "固定版本: $($package.version)"

    if ($DryRun -or $WhatIfPreference) {
        Write-AIStoryPlan -Action "确认许可证后下载到 .partial、校验 SHA-256、原子 Move" -Target $target
        continue
    }
    if (-not (Test-Path -LiteralPath $runtimeMarker -PathType Leaf)) {
        throw "Runtime 根目录未初始化或缺少安全标记，请先运行 Initialize-Runtime.ps1: $root"
    }
    Assert-AIStoryApprovedDownloadPackage -Package $package -Identifier $requestedId

    $confirmation = Read-Host "若接受许可证并确认下载，请完整输入模型 ID '$requestedId'"
    if ($confirmation -cne $requestedId) {
        Write-Warning "未确认，跳过模型包: $requestedId"
        continue
    }

    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq ([string]$package.sha256).ToLowerInvariant()) {
            Write-Host '正式模型文件已存在且校验通过，跳过下载。' -ForegroundColor Green
            $recordPath = Write-ModelInstallRecord `
                -Package $package `
                -Identifier $requestedId `
                -Target $target `
                -ExpectedHash $existingHash `
                -Status 'existing_verified'
            Write-Host "校验记录: $recordPath" -ForegroundColor Cyan
            continue
        }
        throw "目标文件已存在但哈希不匹配；为保护用户文件，脚本拒绝覆盖: $target"
    }

    $targetDirectory = Split-Path $target -Parent
    if (-not (Test-Path -LiteralPath $targetDirectory -PathType Container)) {
        if ($PSCmdlet.ShouldProcess($targetDirectory, '创建模型版本目录')) {
            New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
        }
        else {
            Write-Warning "未批准创建目标目录，跳过: $requestedId"
            continue
        }
    }
    $partial = $target + '.partial'
    if ($RestartPartial -and (Test-Path -LiteralPath $partial -PathType Leaf)) {
        $preserved = $partial + '.invalid.' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        if ($PSCmdlet.ShouldProcess($partial, "保留为 $preserved 后重新下载")) {
            Move-Item -LiteralPath $partial -Destination $preserved
        }
    }

    if ($PSCmdlet.ShouldProcess($partial, "从 $($package.url) 下载或续传")) {
        Save-AIStoryHttpFileWithResume `
            -Uri ([uri]$package.url) `
            -PartialPath $partial `
            -ExpectedSizeBytes ([int64]$package.size_bytes)
    }
    if (-not (Test-Path -LiteralPath $partial -PathType Leaf)) {
        throw "下载未产生 .partial 文件: $partial"
    }

    if ($package.size_bytes -and [int64]$package.size_bytes -gt 0) {
        $actualSize = (Get-Item -LiteralPath $partial).Length
        if ($actualSize -ne [int64]$package.size_bytes) {
            throw "文件大小校验失败：期望 $($package.size_bytes)，实际 $actualSize；.partial 已保留。"
        }
    }
    $actualHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedHash = ([string]$package.sha256).ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 校验失败：期望 $expectedHash，实际 $actualHash；.partial 已保留。"
    }

    if ($PSCmdlet.ShouldProcess($target, '校验成功后原子切换为正式模型文件')) {
        Move-Item -LiteralPath $partial -Destination $target
        $recordPath = Write-ModelInstallRecord `
            -Package $package `
            -Identifier $requestedId `
            -Target $target `
            -ExpectedHash $expectedHash `
            -Status 'installed'
        Write-Host "模型包安装完成: $target" -ForegroundColor Green
        Write-Host "安装记录: $recordPath" -ForegroundColor Cyan
    }
}
