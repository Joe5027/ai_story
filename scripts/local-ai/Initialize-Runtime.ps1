[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$RuntimeRoot,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root

Write-Host "AI Story Runtime 根目录: $root" -ForegroundColor Green
foreach ($directory in $layout.Values) {
    if (Test-Path -LiteralPath $directory -PathType Leaf) {
        throw "目录位置已被文件占用: $directory"
    }
    if (Test-Path -LiteralPath $directory -PathType Container) { continue }
    if ($DryRun) {
        Write-AIStoryPlan -Action '创建目录' -Target $directory
    }
    elseif ($PSCmdlet.ShouldProcess($directory, '创建 Runtime 目录')) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
}

$envPath = Join-Path $layout.Config 'runtime-agent.env'
$registryConfigPath = Join-Path $layout.Config 'runtime-agent.toml'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$registryTemplatePath = Join-Path $repoRoot 'runtime_agent\config.example.toml'
if (-not (Test-Path -LiteralPath $registryConfigPath)) {
    if (-not (Test-Path -LiteralPath $registryTemplatePath -PathType Leaf)) {
        throw "找不到 Runtime Agent 固定配置模板: $registryTemplatePath"
    }
    if ($DryRun) {
        Write-AIStoryPlan -Action '复制默认全 Mock 的 Runtime Agent TOML' -Target $registryConfigPath
    }
    elseif ($PSCmdlet.ShouldProcess($registryConfigPath, '创建默认全 Mock 的 Runtime Agent TOML')) {
        Copy-Item -LiteralPath $registryTemplatePath -Destination $registryConfigPath
    }
}
else {
    Write-Host "保留已有固定配置: $registryConfigPath" -ForegroundColor Yellow
}

if (-not (Test-Path -LiteralPath $envPath)) {
    if ($DryRun) {
        Write-AIStoryPlan -Action '生成 Runtime Agent 本地配置和随机令牌' -Target $envPath
    }
    elseif ($PSCmdlet.ShouldProcess($envPath, '生成 Runtime Agent 本地配置和随机令牌')) {
        $token = New-AIStorySecretToken
        $dbPath = Join-Path $layout.Data 'runtime-agent.sqlite3'
        $content = @(
            '# 此文件含 Runtime Agent 访问令牌；不要提交、导出或发送给他人。'
            "RUNTIME_AGENT_BEARER_TOKEN=$token"
            "RUNTIME_AGENT_DB_PATH=$dbPath"
            "RUNTIME_AGENT_ARTIFACTS_DIR=$($layout.Artifacts)"
            "RUNTIME_AGENT_CONFIG_PATH=$registryConfigPath"
            'RUNTIME_AGENT_ALLOW_UNAUTHENTICATED_LOOPBACK=true'
            'RUNTIME_AGENT_REQUIRE_AUTH_NON_LOOPBACK=true'
            'RUNTIME_AGENT_GPU_CAPACITY=1'
            'RUNTIME_AGENT_CPU_MOTION_CAPACITY=2'
            'RUNTIME_AGENT_POLL_INTERVAL_MS=20'
        ) -join [Environment]::NewLine
        [IO.File]::WriteAllText($envPath, $content + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    }
}
else {
    Write-Host "保留已有配置: $envPath" -ForegroundColor Yellow
    $existingEnv = Get-Content -LiteralPath $envPath -Encoding UTF8
    if (-not ($existingEnv | Where-Object { $_ -match '^RUNTIME_AGENT_CONFIG_PATH=' })) {
        Write-Warning "已有环境文件未设置 RUNTIME_AGENT_CONFIG_PATH；请人工加入: RUNTIME_AGENT_CONFIG_PATH=$registryConfigPath"
    }
}

$markerPath = Join-Path $root '.ai-story-runtime-root'
if (-not (Test-Path -LiteralPath $markerPath)) {
    if ($DryRun) {
        Write-AIStoryPlan -Action '写入目录安全标记' -Target $markerPath
    }
    elseif ($PSCmdlet.ShouldProcess($markerPath, '写入目录安全标记')) {
        $marker = [ordered]@{
            schema_version = 1
            created_at = [DateTimeOffset]::Now.ToString('o')
            purpose = 'AI Story local inference runtime'
        } | ConvertTo-Json
        [IO.File]::WriteAllText($markerPath, $marker + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    }
}

Write-Host '初始化完成。已有目录、配置和数据均未覆盖。' -ForegroundColor Green
