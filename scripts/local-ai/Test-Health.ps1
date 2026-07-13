[CmdletBinding()]
param(
    [string]$RuntimeRoot,
    [uri]$AgentUrl = 'http://127.0.0.1:9100',
    [string]$BearerToken,
    [switch]$IncludeOllama,
    [switch]$IncludeComfyUI,
    [switch]$SkipCapabilities,
    [switch]$RequireReady,
    [switch]$AsJson,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$layout = Get-AIStoryRuntimeLayout -RuntimeRoot $root
if (-not $AgentUrl.IsLoopback -and $AgentUrl.Scheme -ne 'https') {
    throw '非回环 Runtime Agent 必须使用 HTTPS。'
}
if ([string]::IsNullOrWhiteSpace($BearerToken)) {
    $envPath = Join-Path $layout.Config 'runtime-agent.env'
    if (Test-Path -LiteralPath $envPath -PathType Leaf) {
        $BearerToken = [string](Read-AIStoryEnvFile -Path $envPath)['RUNTIME_AGENT_BEARER_TOKEN']
    }
}

$checks = [System.Collections.Generic.List[object]]::new()
function Invoke-HealthProbe {
    param([string]$Name, [uri]$Uri, [hashtable]$Headers = @{})
    if ($DryRun) {
        $script:checks.Add([pscustomobject]@{ service = $Name; ok = $null; uri = $Uri.AbsoluteUri; detail = 'dry-run' })
        return
    }
    try {
        $response = Invoke-RestMethod -Uri $Uri -Method Get -Headers $Headers -TimeoutSec 5
        $script:checks.Add([pscustomobject]@{ service = $Name; ok = $true; uri = $Uri.AbsoluteUri; detail = $response })
    }
    catch {
        $script:checks.Add([pscustomobject]@{ service = $Name; ok = $false; uri = $Uri.AbsoluteUri; detail = $_.Exception.Message })
    }
}

$base = $AgentUrl.AbsoluteUri.TrimEnd('/')
Invoke-HealthProbe -Name 'runtime-agent-live' -Uri ([uri]"$base/v1/health/live")
$headers = @{}
if ($BearerToken) { $headers.Authorization = "Bearer $BearerToken" }
Invoke-HealthProbe -Name 'runtime-agent-ready' -Uri ([uri]"$base/v1/health/ready") -Headers $headers
if (-not $SkipCapabilities) {
    Invoke-HealthProbe -Name 'runtime-agent-capabilities' -Uri ([uri]"$base/v1/capabilities") -Headers $headers
}
if ($IncludeOllama) {
    Invoke-HealthProbe -Name 'ollama' -Uri ([uri]'http://127.0.0.1:11434/api/tags')
}
if ($IncludeComfyUI) {
    Invoke-HealthProbe -Name 'comfyui' -Uri ([uri]'http://127.0.0.1:8188/system_stats')
}

if ($AsJson) { $checks | ConvertTo-Json -Depth 8 } else { $checks | Format-Table service, ok, uri, detail -AutoSize }
if ($RequireReady) {
    $ready = $checks | Where-Object service -eq 'runtime-agent-ready' | Select-Object -First 1
    if (-not $ready -or $ready.ok -ne $true) { throw 'Runtime Agent 未就绪。' }
    if (-not $SkipCapabilities) {
        $capabilities = $checks | Where-Object service -eq 'runtime-agent-capabilities' | Select-Object -First 1
        if (-not $capabilities -or $capabilities.ok -ne $true) {
            throw 'Runtime Agent 已响应，但能力发现未通过。'
        }
    }
}
