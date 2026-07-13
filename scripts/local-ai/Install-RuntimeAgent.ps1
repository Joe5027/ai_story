[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$RuntimeRoot,
    [string]$RuntimeAgentSource,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root
$runtimeMarker = Join-Path $root '.ai-story-runtime-root'
if ([string]::IsNullOrWhiteSpace($RuntimeAgentSource)) {
    $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $RuntimeAgentSource = Join-Path $repoRoot 'runtime_agent'
}
$source = [IO.Path]::GetFullPath($RuntimeAgentSource)
$pyproject = Join-Path $source 'pyproject.toml'
$uvLock = Join-Path $source 'uv.lock'
if (-not (Test-Path -LiteralPath $pyproject -PathType Leaf)) {
    throw "找不到 Runtime Agent pyproject.toml: $pyproject"
}
if (-not (Test-Path -LiteralPath $uvLock -PathType Leaf)) {
    throw "找不到 Runtime Agent 独立依赖锁: $uvLock"
}
if (-not (Test-Path -LiteralPath $layout.Root -PathType Container) -and -not ($DryRun -or $WhatIfPreference)) {
    throw "Runtime 根目录尚未初始化，请先运行 Initialize-Runtime.ps1: $root"
}
if (-not ($DryRun -or $WhatIfPreference) -and -not (Test-Path -LiteralPath $runtimeMarker -PathType Leaf)) {
    throw "Runtime 根目录缺少安全标记，请先运行 Initialize-Runtime.ps1: $runtimeMarker"
}

$componentRoot = Join-Path $layout.Components 'runtime-agent'
$venvRoot = Join-Path $componentRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
$lockExport = Join-Path $layout.Config 'runtime-agent-requirements.lock.txt'

$python = Get-Command python -ErrorAction SilentlyContinue
$pythonArgs = @()
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) { $pythonArgs = @('-3.11') }
}
if (-not $python -and -not ($DryRun -or $WhatIfPreference)) {
    throw '找不到 Python 3.11+。请先运行 Test-Prerequisites.ps1。'
}
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv -and -not ($DryRun -or $WhatIfPreference)) {
    throw '找不到 uv。Runtime Agent 安装必须从独立 uv.lock 冻结同步，拒绝退回未锁定的 pip 解析。'
}
$uvPath = if ($uv) { $uv.Source } else { 'uv' }
$pythonPath = if ($python) { $python.Source } else { 'python' }
if (-not ($DryRun -or $WhatIfPreference)) {
    $versionArgs = @($pythonArgs) + @('-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    $version = (& $pythonPath @versionArgs 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^3\.(1[1-9]|[2-9][0-9])$') {
        throw "Runtime Agent 需要 Python 3.11+，当前检测结果: $version"
    }
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $args = @($pythonArgs) + @('-m', 'venv', $venvRoot)
    if ($DryRun) {
        Invoke-AIStoryProcess -FilePath $pythonPath -ArgumentList $args -DryRun
    }
    elseif ($PSCmdlet.ShouldProcess($venvRoot, '创建独立 Runtime Agent Python 虚拟环境')) {
        New-Item -ItemType Directory -Path $componentRoot -Force | Out-Null
        Invoke-AIStoryProcess -FilePath $pythonPath -ArgumentList $args
    }
}
else {
    Write-Host "复用已有虚拟环境: $venvRoot" -ForegroundColor Yellow
}

$exportArgs = @(
    'export', '--project', $source, '--frozen', '--no-dev', '--no-emit-project',
    '--output-file', $lockExport
)
$syncArgs = @('pip', 'sync', '--python', $venvPython, '--require-hashes', $lockExport)
$installArgs = @('pip', 'install', '--python', $venvPython, '--no-deps', $source)
if ($DryRun) {
    Invoke-AIStoryProcess -FilePath $uvPath -ArgumentList $exportArgs -WorkingDirectory $source -DryRun
    Invoke-AIStoryProcess -FilePath $uvPath -ArgumentList $syncArgs -WorkingDirectory $source -DryRun
    Invoke-AIStoryProcess -FilePath $uvPath -ArgumentList $installArgs -WorkingDirectory $source -DryRun
}
elseif ($PSCmdlet.ShouldProcess($source, "安装到独立环境 $venvRoot")) {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "虚拟环境创建失败: $venvPython"
    }
    # 先从独立 uv.lock 冻结导出并同步精确依赖，再以 --no-deps 安装本地 Agent 包，
    # 避免 pip 在部署机上依据宽版本范围重新求解出另一套环境。
    Invoke-AIStoryProcess -FilePath $uvPath -ArgumentList $exportArgs -WorkingDirectory $source
    Invoke-AIStoryProcess -FilePath $uvPath -ArgumentList $syncArgs -WorkingDirectory $source
    Invoke-AIStoryProcess -FilePath $uvPath -ArgumentList $installArgs -WorkingDirectory $source

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $installRecord = Join-Path $layout.Config "runtime-agent-install.$stamp.json"
    $record = [ordered]@{
        schema_version = 1
        installed_at = [DateTimeOffset]::Now.ToString('o')
        source = $source
        source_git_commit = $(
            try { (& git -C $source rev-parse HEAD 2>$null | Select-Object -First 1) } catch { $null }
        )
        uv_lock_sha256 = (Get-FileHash -LiteralPath $uvLock -Algorithm SHA256).Hash.ToLowerInvariant()
        exported_requirements_sha256 = (Get-FileHash -LiteralPath $lockExport -Algorithm SHA256).Hash.ToLowerInvariant()
        python = $venvPython
    } | ConvertTo-Json
    [IO.File]::WriteAllText($installRecord, $record + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

Write-Host 'Runtime Agent 安装步骤完成。脚本未安装或下载 Ollama、ComfyUI、LightX2V 和模型。' -ForegroundColor Green
