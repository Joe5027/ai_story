[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$RuntimeRoot,
    [string]$ManifestPath = (Join-Path $PSScriptRoot 'components.example.json'),
    [string[]]$ComponentId,
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
    throw "找不到组件清单: $manifestFullPath"
}
$manifest = Get-Content -LiteralPath $manifestFullPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or -not $manifest.components) {
    throw '组件清单 schema_version 必须为 1，且必须包含 components。'
}

$components = @($manifest.components)
$duplicateIds = @($components | Group-Object id | Where-Object Count -gt 1)
if ($duplicateIds.Count -gt 0) { throw "组件清单含重复 ID: $($duplicateIds.Name -join ', ')" }
if (-not $ComponentId -or $ComponentId.Count -eq 0) {
    Write-Host '未指定 -ComponentId：仅显示清单，不会下载或安装任何内容。' -ForegroundColor Yellow
    $components | Select-Object id, name, component, version, install_mode, size_display, download_enabled | Format-Table -AutoSize
    return
}

foreach ($requestedId in $ComponentId) {
    if ($requestedId -notmatch '^[a-z0-9][a-z0-9._-]{1,79}$') {
        throw "组件 ID 格式无效: $requestedId"
    }
    $component = $components | Where-Object id -eq $requestedId | Select-Object -First 1
    if (-not $component) { throw "组件清单不存在 ID: $requestedId" }

    $installMode = [string]$component.install_mode
    if ($installMode -notin @('archive', 'download_only')) {
        throw "组件 $requestedId 的 install_mode 只允许 archive 或 download_only。"
    }
    $fileName = [string]$component.file_name
    if ([string]::IsNullOrWhiteSpace($fileName) -or [IO.Path]::GetFileName($fileName) -cne $fileName) {
        throw "组件 $requestedId 的 file_name 必须是单一文件名。"
    }
    if ($installMode -eq 'archive' -and [IO.Path]::GetExtension($fileName) -ne '.zip') {
        throw "archive 组件当前只接受 .zip 固定包: $fileName"
    }

    $target = Resolve-AIStoryChildPath -Parent $layout.Components -RelativePath ([string]$component.target_relative_path)
    $packageDirectory = Resolve-AIStoryChildPath -Parent $layout.Cache -RelativePath "component-packages/$requestedId"
    $verifiedPackage = Join-Path $packageDirectory $fileName
    $partial = $verifiedPackage + '.partial'

    Write-Host ''
    Write-Host "运行组件: $($component.name) [$requestedId]" -ForegroundColor Green
    Write-Host "组件类型: $($component.component)"
    Write-Host "许可证: $($component.license)"
    Write-Host "许可证链接: $($component.license_url)"
    Write-Host "下载大小: $($component.size_display)"
    Write-Host "固定版本: $($component.version)"
    Write-Host "安装模式: $installMode"
    Write-Host "组件目标: $target"
    Write-Host "校验包路径: $verifiedPackage"

    if ($DryRun -or $WhatIfPreference) {
        $action = if ($installMode -eq 'archive') {
            '逐包确认后续传下载、校验 SHA-256、解压到临时目录并原子切换版本目录'
        }
        else {
            '逐包确认后续传下载并校验 SHA-256；只暂存官方安装器，不静默执行'
        }
        Write-AIStoryPlan -Action $action -Target $target
        continue
    }
    if (-not (Test-Path -LiteralPath $runtimeMarker -PathType Leaf)) {
        throw "Runtime 根目录未初始化或缺少安全标记，请先运行 Initialize-Runtime.ps1: $root"
    }
    Assert-AIStoryApprovedDownloadPackage -Package $component -Identifier $requestedId

    $confirmation = Read-Host "若接受许可证并确认处理，请完整输入组件 ID '$requestedId'"
    if ($confirmation -cne $requestedId) {
        Write-Warning "未确认，跳过运行组件: $requestedId"
        continue
    }

    if ($installMode -eq 'archive' -and (Test-Path -LiteralPath $target)) {
        $existingRecordPath = Join-Path $target 'component-install.json'
        if (Test-Path -LiteralPath $existingRecordPath -PathType Leaf) {
            $existingRecord = Get-Content -LiteralPath $existingRecordPath -Raw | ConvertFrom-Json
            if ([string]$existingRecord.sha256 -eq ([string]$component.sha256).ToLowerInvariant()) {
                Write-Host '目标组件版本已存在且安装记录匹配，跳过。' -ForegroundColor Green
                continue
            }
        }
        throw "组件目标已存在但安装记录不匹配；脚本拒绝覆盖，请使用新的版本目录: $target"
    }

    if (-not (Test-Path -LiteralPath $packageDirectory -PathType Container)) {
        if ($PSCmdlet.ShouldProcess($packageDirectory, '创建组件包缓存目录')) {
            New-Item -ItemType Directory -Path $packageDirectory -Force | Out-Null
        }
        else { continue }
    }
    if ($RestartPartial -and (Test-Path -LiteralPath $partial -PathType Leaf)) {
        $preserved = $partial + '.invalid.' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
        if ($PSCmdlet.ShouldProcess($partial, "保留为 $preserved 后重新下载")) {
            Move-Item -LiteralPath $partial -Destination $preserved
        }
    }

    $expectedHash = ([string]$component.sha256).ToLowerInvariant()
    $verified = $false
    if (Test-Path -LiteralPath $verifiedPackage -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $verifiedPackage -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -ne $expectedHash) {
            throw "组件包缓存已存在但哈希不匹配；为保护用户文件，脚本拒绝覆盖: $verifiedPackage"
        }
        $verified = $true
    }
    if (-not $verified) {
        if ($PSCmdlet.ShouldProcess($partial, "从 $($component.url) 下载或续传")) {
            Save-AIStoryHttpFileWithResume `
                -Uri ([uri]$component.url) `
                -PartialPath $partial `
                -ExpectedSizeBytes ([int64]$component.size_bytes)
        }
        if (-not (Test-Path -LiteralPath $partial -PathType Leaf)) {
            throw "下载未产生 .partial 文件: $partial"
        }
        $actualSize = (Get-Item -LiteralPath $partial).Length
        if ($actualSize -ne [int64]$component.size_bytes) {
            throw "文件大小校验失败：期望 $($component.size_bytes)，实际 $actualSize；.partial 已保留。"
        }
        $actualHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $expectedHash) {
            throw "SHA-256 校验失败：期望 $expectedHash，实际 $actualHash；.partial 已保留。"
        }
        if ($PSCmdlet.ShouldProcess($verifiedPackage, '校验成功后原子切换为已验证组件包')) {
            Move-Item -LiteralPath $partial -Destination $verifiedPackage
        }
    }

    $recordData = [ordered]@{
        schema_version = 1
        installed_at = [DateTimeOffset]::Now.ToString('o')
        component_id = $requestedId
        component = [string]$component.component
        version = [string]$component.version
        license = [string]$component.license
        license_url = [string]$component.license_url
        install_mode = $installMode
        size_bytes = [int64]$component.size_bytes
        sha256 = $expectedHash
        verified_package = $verifiedPackage
        target = $target
        # 清单 URL 可能含短期签名查询参数；安装记录只保留不含 query/fragment 的来源路径。
        source_url = ([uri][string]$component.url).GetLeftPart([UriPartial]::Path)
        source_manifest = $manifestFullPath
        source_manifest_sha256 = (Get-FileHash -LiteralPath $manifestFullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    if ($installMode -eq 'download_only') {
        $recordPath = Join-Path $layout.Config "component-install.$requestedId.$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
        [IO.File]::WriteAllText($recordPath, ($recordData | ConvertTo-Json -Depth 4) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        Write-Host "组件安装包已校验并暂存（未执行）: $verifiedPackage" -ForegroundColor Green
        Write-Host '请人工核对 Authenticode 发布者后按官方交互式流程安装。' -ForegroundColor Yellow
        continue
    }

    $targetParent = Split-Path $target -Parent
    if (-not (Test-Path -LiteralPath $targetParent -PathType Container)) {
        New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
    }
    $staging = Join-Path $layout.Staging ("component-$requestedId-" + [Guid]::NewGuid().ToString('N'))
    if ($PSCmdlet.ShouldProcess($target, '解压到临时目录并原子切换为固定版本组件目录')) {
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        try {
            Expand-Archive -LiteralPath $verifiedPackage -DestinationPath $staging
            [IO.File]::WriteAllText(
                (Join-Path $staging 'component-install.json'),
                ($recordData | ConvertTo-Json -Depth 4) + [Environment]::NewLine,
                [Text.UTF8Encoding]::new($false)
            )
            Move-Item -LiteralPath $staging -Destination $target
        }
        catch {
            Write-Warning "组件解压或切换失败；临时目录保留供诊断: $staging"
            throw
        }
        $recordPath = Join-Path $layout.Config "component-install.$requestedId.$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
        [IO.File]::WriteAllText($recordPath, ($recordData | ConvertTo-Json -Depth 4) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        Write-Host "固定组件版本安装完成: $target" -ForegroundColor Green
        Write-Host '脚本未修改 current 指针；完成健康检查和基准后再人工更新 Agent 固定配置。' -ForegroundColor Yellow
    }
}
