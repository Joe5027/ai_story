[CmdletBinding()]
param(
    [string]$RuntimeRoot,
    [switch]$Strict,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force

$root = Get-AIStoryRuntimeRoot -RuntimeRoot $RuntimeRoot
$results = [System.Collections.Generic.List[object]]::new()

function Add-CheckResult {
    param([string]$Name, [bool]$Passed, [string]$Value, [bool]$Required)
    $script:results.Add([pscustomobject]@{
        Check = $Name
        Passed = $Passed
        Required = $Required
        Value = $Value
    })
}

Add-CheckResult -Name 'Windows' -Passed $IsWindows -Value ([Environment]::OSVersion.VersionString) -Required $true
Add-CheckResult -Name 'PowerShell' -Passed ($PSVersionTable.PSVersion.Major -ge 7) -Value $PSVersionTable.PSVersion.ToString() -Required $true

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
$pythonVersion = '未安装'
$pythonOk = $false
if ($python) {
    try {
        $versionArgs = if ($python.Name -eq 'py.exe' -or $python.Name -eq 'py') { @('-3.11', '--version') } else { @('--version') }
        $pythonVersion = (& $python.Source @versionArgs 2>&1 | Out-String).Trim()
        $pythonOk = $pythonVersion -match 'Python 3\.(1[1-9]|[2-9][0-9])'
    }
    catch { $pythonVersion = $_.Exception.Message }
}
Add-CheckResult -Name 'Python >= 3.11' -Passed $pythonOk -Value $pythonVersion -Required $true

foreach ($tool in @('git', 'uv', 'ffmpeg', 'nvidia-smi', 'ollama')) {
    $command = Get-Command $tool -ErrorAction SilentlyContinue
    Add-CheckResult -Name $tool -Passed ([bool]$command) -Value $(if ($command) { $command.Source } else { '未安装（可按需安装）' }) -Required ($tool -in @('git', 'uv'))
}

$driveRoot = [IO.Path]::GetPathRoot($root)
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($driveRoot.TrimEnd('\'))'" -ErrorAction SilentlyContinue
$diskValue = if ($drive) { '{0:N1} GB 可用 / {1:N1} GB' -f ($drive.FreeSpace / 1GB), ($drive.Size / 1GB) } else { '目标磁盘当前不可用' }
Add-CheckResult -Name 'Runtime 磁盘' -Passed ([bool]$drive) -Value $diskValue -Required $true

foreach ($port in @(9100, 11434, 8188)) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    Add-CheckResult -Name "端口 $port" -Passed (-not [bool]$listener) -Value $(if ($listener) { '已被占用（可能是已运行服务）' } else { '可用' }) -Required ($port -eq 9100)
}

$results | Format-Table -AutoSize
if ($DryRun) {
    Write-AIStoryPlan -Action '仅执行只读预检' -Target $root
}

$failedRequired = @($results | Where-Object { $_.Required -and -not $_.Passed })
if ($Strict -and $failedRequired.Count -gt 0) {
    throw "预检失败: $($failedRequired.Check -join ', ')"
}
if ($failedRequired.Count -gt 0) {
    Write-Warning "存在必需项未满足: $($failedRequired.Check -join ', ')"
}
else {
    Write-Host '基础预检通过。模型和可选运行时仍需单独确认安装。' -ForegroundColor Green
}
