Set-StrictMode -Version Latest

$script:DefaultRuntimeRoot = 'E:\AI\ai-story-runtime'

function Get-AIStoryRuntimeRoot {
    [CmdletBinding()]
    param([string]$RuntimeRoot)

    $candidate = $RuntimeRoot
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        # 与 Django 的 AI_RUNTIME_ROOT 保持同一主变量；旧脚本变量只作为兼容回退。
        $candidate = $env:AI_RUNTIME_ROOT
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = $env:AI_STORY_RUNTIME_ROOT
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = $script:DefaultRuntimeRoot
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($candidate.Trim())
    $fullPath = [IO.Path]::GetFullPath($expanded).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $pathRoot = [IO.Path]::GetPathRoot($fullPath).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    if ($fullPath -eq $pathRoot) {
        throw "RuntimeRoot 不能是磁盘根目录: $fullPath"
    }
    return $fullPath
}

function Get-AIStoryRuntimeLayout {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RuntimeRoot)

    $root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
    return [ordered]@{
        Root       = $root
        Models     = Join-Path $root 'models'
        Workflows  = Join-Path $root 'workflows'
        Cache      = Join-Path $root 'cache'
        Data       = Join-Path $root 'data'
        Staging    = Join-Path $root 'staging'
        Logs       = Join-Path $root 'logs'
        Config     = Join-Path $root 'config'
        Run        = Join-Path $root 'run'
        Backups    = Join-Path $root 'backups'
        Components = Join-Path $root 'components'
        Artifacts  = Join-Path $root 'artifacts'
    }
}

function Resolve-AIStoryChildPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Parent,
        [Parameter(Mandatory)][string]$RelativePath
    )

    if ([IO.Path]::IsPathRooted($RelativePath)) {
        throw "目标路径必须是相对路径: $RelativePath"
    }
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $childFull = [IO.Path]::GetFullPath((Join-Path $parentFull $RelativePath))
    $prefix = $parentFull + [IO.Path]::DirectorySeparatorChar
    if (-not $childFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "目标路径越过了允许目录: $RelativePath"
    }
    return $childFull
}

function Write-AIStoryPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Action,
        [Parameter(Mandatory)][string]$Target
    )

    Write-Host "[PLAN] $Action -> $Target" -ForegroundColor Cyan
}

function Invoke-AIStoryProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory,
        [switch]$DryRun
    )

    $displayArgs = ($ArgumentList | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    if ($DryRun) {
        $targetDirectory = if ($WorkingDirectory) { $WorkingDirectory } else { (Get-Location).Path }
        Write-AIStoryPlan -Action "执行 $FilePath $displayArgs" -Target $targetDirectory
        return
    }

    $command = Get-Command $FilePath -ErrorAction SilentlyContinue
    if (-not $command -and -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw "找不到可执行文件: $FilePath"
    }
    if ($WorkingDirectory) {
        Push-Location -LiteralPath $WorkingDirectory
    }
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "命令退出码为 ${LASTEXITCODE}: $FilePath $displayArgs"
        }
    }
    finally {
        if ($WorkingDirectory) { Pop-Location }
    }
}

function Read-AIStoryEnvFile {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    $values = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $separator = $trimmed.IndexOf('=')
        if ($separator -lt 1) { throw "无效环境变量行: $line" }
        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if ($name -notmatch '^[A-Z][A-Z0-9_]*$') {
            throw "无效环境变量名: $name"
        }
        if ($value.Contains("`n") -or $value.Contains("`r")) {
            throw "环境变量值不允许换行: $name"
        }
        $values[$name] = $value
    }
    return $values
}

function New-AIStorySecretToken {
    [CmdletBinding()]
    param([ValidateRange(32, 128)][int]$ByteLength = 48)

    $bytes = New-Object byte[] $ByteLength
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Assert-AIStoryApprovedDownloadPackage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Package,
        [Parameter(Mandatory)][string]$Identifier
    )

    if (-not [bool]$Package.download_enabled) {
        throw "包 $Identifier 尚未在清单中启用。请先核对许可证、URL、固定版本、精确大小和 SHA-256。"
    }

    $url = [string]$Package.url
    $downloadUri = $null
    if (-not [uri]::TryCreate($url, [UriKind]::Absolute, [ref]$downloadUri) -or
        $downloadUri.Scheme -ne 'https' -or
        -not [string]::IsNullOrEmpty($downloadUri.UserInfo) -or
        $downloadUri.Host -eq 'example.invalid') {
        throw "只允许非占位 HTTPS 下载地址: $url"
    }

    $license = [string]$Package.license
    $licenseUrl = [string]$Package.license_url
    $licenseUri = $null
    if ([string]::IsNullOrWhiteSpace($license) -or $license -match '^(请|待|TBD|TODO)') {
        throw "包 $Identifier 尚未登记经过复核的许可证名称。"
    }
    if (-not [uri]::TryCreate($licenseUrl, [UriKind]::Absolute, [ref]$licenseUri) -or
        $licenseUri.Scheme -ne 'https' -or
        -not [string]::IsNullOrEmpty($licenseUri.UserInfo)) {
        throw "包 $Identifier 的许可证链接必须是 HTTPS 官方地址。"
    }

    $version = [string]$Package.version
    if ([string]::IsNullOrWhiteSpace($version) -or $version -match '(?i)PIN-|PLACEHOLDER|REPLACE|LATEST|TBD|TODO') {
        throw "包 $Identifier 尚未固定可回滚的版本或摘要。"
    }
    if ([string]::IsNullOrWhiteSpace([string]$Package.size_display) -or
        [string]$Package.size_display -match '启用前|以官方文件为准') {
        throw "包 $Identifier 尚未填写可供操作者核对的下载大小。"
    }
    if ($null -eq $Package.size_bytes -or [int64]$Package.size_bytes -le 0) {
        throw "包 $Identifier 必须填写精确且大于 0 的 size_bytes。"
    }

    $sha256 = [string]$Package.sha256
    if ($sha256 -notmatch '^[0-9a-fA-F]{64}$' -or $sha256 -match '^0{64}$') {
        throw "包 $Identifier 的 SHA-256 无效或仍是零值占位符。"
    }
}

function Save-AIStoryHttpFileWithResume {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][uri]$Uri,
        [Parameter(Mandatory)][string]$PartialPath,
        [ValidateRange(1, [long]::MaxValue)][long]$ExpectedSizeBytes
    )

    if ($Uri.Scheme -ne 'https') { throw "只允许 HTTPS 下载: $Uri" }
    $offset = if (Test-Path -LiteralPath $PartialPath -PathType Leaf) {
        (Get-Item -LiteralPath $PartialPath).Length
    }
    else { 0L }
    if ($ExpectedSizeBytes -gt 0 -and $offset -gt $ExpectedSizeBytes) {
        throw "已有 .partial 大于清单精确大小：期望最多 $ExpectedSizeBytes，实际 $offset。"
    }

    $client = [Net.Http.HttpClient]::new()
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $Uri)
    $response = $null
    try {
        if ($offset -gt 0) {
            $request.Headers.Range = [Net.Http.Headers.RangeHeaderValue]::new($offset, $null)
            Write-Host "从已有 .partial 的 $offset 字节位置尝试续传。" -ForegroundColor Cyan
        }
        $response = $client.SendAsync(
            $request,
            [Net.Http.HttpCompletionOption]::ResponseHeadersRead
        ).GetAwaiter().GetResult()
        if ($response.StatusCode -eq [Net.HttpStatusCode]::RequestedRangeNotSatisfiable) {
            Write-Warning '服务器返回 416，将直接校验现有 .partial；若校验失败请使用重新下载开关。'
            return
        }
        $response.EnsureSuccessStatusCode()
        if ($response.RequestMessage.RequestUri.Scheme -ne 'https' -or
            -not [string]::IsNullOrEmpty($response.RequestMessage.RequestUri.UserInfo)) {
            throw "下载重定向到了非 HTTPS 地址，拒绝继续: $($response.RequestMessage.RequestUri)"
        }

        $append = $offset -gt 0 -and $response.StatusCode -eq [Net.HttpStatusCode]::PartialContent
        if ($append) {
            $rangeStart = $response.Content.Headers.ContentRange.From
            if ($null -eq $rangeStart -or [int64]$rangeStart -ne $offset) {
                throw "服务器 Content-Range 起点与本地 .partial 不一致：期望 $offset，实际 $rangeStart。"
            }
            $rangeTotal = $response.Content.Headers.ContentRange.Length
            if ($ExpectedSizeBytes -gt 0 -and $null -ne $rangeTotal -and [int64]$rangeTotal -ne $ExpectedSizeBytes) {
                throw "服务器 Content-Range 总大小与清单不一致：期望 $ExpectedSizeBytes，实际 $rangeTotal。"
            }
        }
        elseif ($offset -gt 0) {
            Write-Warning '服务器未接受 Range，本次会从头写入同一 .partial（不会改动正式文件）。'
        }
        $contentLength = $response.Content.Headers.ContentLength
        $expectedResponseBytes = if ($append) { $ExpectedSizeBytes - $offset } else { $ExpectedSizeBytes }
        if ($ExpectedSizeBytes -gt 0 -and $null -ne $contentLength -and [int64]$contentLength -ne $expectedResponseBytes) {
            throw "服务器 Content-Length 与清单不一致：期望 $expectedResponseBytes，实际 $contentLength。"
        }

        $mode = if ($append) { [IO.FileMode]::Append } else { [IO.FileMode]::Create }
        $input = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $output = [IO.FileStream]::new($PartialPath, $mode, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $input.CopyToAsync($output).GetAwaiter().GetResult() }
        finally {
            $output.Dispose()
            $input.Dispose()
        }
    }
    finally {
        if ($response) { $response.Dispose() }
        $request.Dispose()
        $client.Dispose()
    }
}

Export-ModuleMember -Function @(
    'Get-AIStoryRuntimeRoot',
    'Get-AIStoryRuntimeLayout',
    'Resolve-AIStoryChildPath',
    'Write-AIStoryPlan',
    'Invoke-AIStoryProcess',
    'Read-AIStoryEnvFile',
    'New-AIStorySecretToken',
    'Assert-AIStoryApprovedDownloadPackage',
    'Save-AIStoryHttpFileWithResume'
)
