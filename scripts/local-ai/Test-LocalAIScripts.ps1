[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$failures = [System.Collections.Generic.List[string]]::new()
function Assert-Contract {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { $script:failures.Add($Message) }
}

Write-Host '1/3 PowerShell 语法检查' -ForegroundColor Cyan
$powershellFiles = Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object Extension -in @('.ps1', '.psm1')
foreach ($file in $powershellFiles) {
    $tokens = $null
    $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
    foreach ($parseError in @($parseErrors)) {
        $failures.Add("$($file.Name): $($parseError.Message)")
    }
}

Write-Host '2/3 安全契约检查' -ForegroundColor Cyan
$modelScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Install-ModelPack.ps1') -Raw
foreach ($requiredText in @('.partial', 'Get-FileHash', 'SHA-256', 'Move-Item', 'Read-Host', 'model-install.')) {
    Assert-Contract -Condition $modelScript.Contains($requiredText) -Message "模型下载脚本缺少契约: $requiredText"
}
$commonModule = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Common.psm1') -Raw
foreach ($requiredText in @('AI_RUNTIME_ROOT', 'AI_STORY_RUNTIME_ROOT', 'ContentRange.From', 'example.invalid', "'^0{64}`$'", 'size_bytes')) {
    Assert-Contract -Condition $commonModule.Contains($requiredText) -Message "公共下载安全契约缺少: $requiredText"
}
$runtimeInstallScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Install-RuntimeAgent.ps1') -Raw
foreach ($requiredText in @('uv.lock', '--frozen', '--require-hashes', '--no-deps', 'uv_lock_sha256')) {
    Assert-Contract -Condition $runtimeInstallScript.Contains($requiredText) -Message "Runtime Agent 冻结安装契约缺少: $requiredText"
}
$startScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Start-LocalAI.ps1') -Raw
foreach ($requiredText in @('/v1/health/live', '/v1/health/ready', '/v1/capabilities', 'WindowStyle Hidden')) {
    Assert-Contract -Condition $startScript.Contains($requiredText) -Message "Runtime Agent 启动/就绪契约缺少: $requiredText"
}
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.example.json') -Raw | ConvertFrom-Json
Assert-Contract -Condition ($manifest.schema_version -eq 1) -Message '示例模型清单版本不是 1'
Assert-Contract -Condition (@($manifest.packages).Count -ge 4) -Message '示例模型包数量不足'
Assert-Contract -Condition (-not (@($manifest.packages) | Where-Object download_enabled)) -Message '示例清单不得默认启用下载'
$componentScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Install-RuntimeComponent.ps1') -Raw
foreach ($requiredText in @('.partial', 'Read-Host', 'Expand-Archive', 'component-install.json', 'download_only', 'archive')) {
    Assert-Contract -Condition $componentScript.Contains($requiredText) -Message "组件安装脚本缺少契约: $requiredText"
}
$componentManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'components.example.json') -Raw | ConvertFrom-Json
Assert-Contract -Condition ($componentManifest.schema_version -eq 1) -Message '示例组件清单版本不是 1'
Assert-Contract -Condition (@($componentManifest.components).Count -ge 4) -Message '示例组件清单数量不足'
Assert-Contract -Condition (-not (@($componentManifest.components) | Where-Object download_enabled)) -Message '示例组件清单不得默认启用下载'

foreach ($fileName in @(
    'Initialize-Runtime.ps1',
    'Install-RuntimeAgent.ps1',
    'Install-RuntimeComponent.ps1',
    'Install-ModelPack.ps1',
    'Start-LocalAI.ps1',
    'Stop-LocalAI.ps1',
    'Backup-Configuration.ps1',
    'Collect-Logs.ps1'
)) {
    $content = Get-Content -LiteralPath (Join-Path $PSScriptRoot $fileName) -Raw
    Assert-Contract -Condition ($content -match 'SupportsShouldProcess\s*=\s*\$true') -Message "$fileName 未声明 SupportsShouldProcess"
    Assert-Contract -Condition ($content -match '\[switch\]\$DryRun') -Message "$fileName 未声明 DryRun"
    Assert-Contract -Condition (-not ($content -match '(?im)^\s*Remove-Item\b')) -Message "$fileName 不应删除文件"
}

Write-Host '3/3 无副作用 DryRun / WhatIf 检查' -ForegroundColor Cyan
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtimeSource = Join-Path $repoRoot 'runtime_agent'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('ai-story-script-contract-' + [Guid]::NewGuid().ToString('N'))

& (Join-Path $PSScriptRoot 'Test-Prerequisites.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Initialize-Runtime.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Initialize-Runtime.ps1') -RuntimeRoot $testRoot -WhatIf
& (Join-Path $PSScriptRoot 'Install-RuntimeAgent.ps1') -RuntimeRoot $testRoot -RuntimeAgentSource $runtimeSource -DryRun
& (Join-Path $PSScriptRoot 'Install-RuntimeAgent.ps1') -RuntimeRoot $testRoot -RuntimeAgentSource $runtimeSource -WhatIf
& (Join-Path $PSScriptRoot 'Install-RuntimeComponent.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Install-RuntimeComponent.ps1') -RuntimeRoot $testRoot -ComponentId 'comfyui-windows-portable' -DryRun
& (Join-Path $PSScriptRoot 'Install-RuntimeComponent.ps1') -RuntimeRoot $testRoot -ComponentId 'comfyui-windows-portable' -WhatIf
& (Join-Path $PSScriptRoot 'Install-ModelPack.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Install-ModelPack.ps1') -RuntimeRoot $testRoot -ModelId 'flux2-klein-4b' -DryRun
& (Join-Path $PSScriptRoot 'Install-ModelPack.ps1') -RuntimeRoot $testRoot -ModelId 'flux2-klein-4b' -WhatIf
& (Join-Path $PSScriptRoot 'Start-LocalAI.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Stop-LocalAI.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Test-Health.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Backup-Configuration.ps1') -RuntimeRoot $testRoot -DryRun
& (Join-Path $PSScriptRoot 'Collect-Logs.ps1') -RuntimeRoot $testRoot -DryRun
Assert-Contract -Condition (-not (Test-Path -LiteralPath $testRoot)) -Message 'DryRun/WhatIf 不应创建测试 Runtime 目录'

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    throw "本地 AI 脚本契约检查失败，共 $($failures.Count) 项。"
}
Write-Host "本地 AI 脚本契约检查通过：$($powershellFiles.Count) 个 PowerShell 文件。" -ForegroundColor Green
