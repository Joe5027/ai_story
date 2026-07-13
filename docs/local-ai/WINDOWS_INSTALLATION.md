# Windows 本地 AI 安装指南

> 状态：安装步骤和验证合同。仓库已包含 Runtime Agent Mock 源码，但不存在真实模型已经安装的证据；执行者必须在目标 Windows 主机逐项记录结果。

## 1. 安装目标

默认在以下位置部署：

```text
E:\AI\ai-story-runtime\
├─ config\
├─ logs\
├─ models\
├─ workflows\
├─ cache\
│  └─ component-packages\
├─ data\
├─ staging\
├─ artifacts\
├─ components\
├─ run\
└─ backups\
```

服务端口：Runtime Agent 9100、Ollama 11434、ComfyUI 8188。默认只监听 `127.0.0.1`。

## 2. 分层安装总流程

仓库脚本默认不下载模型、不安装第三方运行时，并同时支持 `-WhatIf` 和无副作用 `-DryRun`。先在仓库根目录执行脚本合同测试：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Test-LocalAIScripts.ps1
```

然后按以下层次执行；每层都先预演：

1. 只读环境预检；
2. 初始化目录与全 Mock 配置；
3. 安装独立且锁定依赖的 Runtime Agent；
4. 从管理员批准的组件清单逐个安装/暂存 Ollama、ComfyUI、LightX2V、FFmpeg；
5. 从管理员批准的模型清单逐包下载模型；
6. 固定 workflow 和 Adapter 配置；
7. 启动、健康检查、Mock 契约测试；
8. 单模型真实基准通过后才启用对应能力。

运行根目录的主环境变量与 Django 保持一致：

```powershell
$env:AI_RUNTIME_ROOT = 'E:\AI\ai-story-runtime'
```

旧脚本变量 `AI_STORY_RUNTIME_ROOT` 仅作为兼容回退；两者同时存在时以 `AI_RUNTIME_ROOT` 为准。每个脚本的 `-RuntimeRoot` 参数优先级最高。

## 3. 前置检查

优先使用只读脚本：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Test-Prerequisites.ps1
pwsh -NoProfile -File .\scripts\local-ai\Test-Prerequisites.ps1 -Strict
```

脚本检查 Windows、PowerShell 7、Python 3.11+、Git、uv、目标磁盘、端口以及可选的 FFmpeg/NVIDIA/Ollama。需要人工补充核对时再执行：

```powershell
$PSVersionTable.PSVersion
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM
Get-PSDrive -Name E | Select-Object Name, Used, Free
Get-Command git, python, nvidia-smi -ErrorAction SilentlyContinue
```

检查项：

- `E:` 盘存在且有足够空间存放模型、缓存和生成物；
- NVIDIA 驱动与目标 PyTorch/CUDA 组合兼容；
- 系统 RAM、显存和散热适合所选模型；
- 端口 9100/11434/8188 未被占用；
- 安装包来源、校验值和模型许可证已经审查。

硬件满足最低值不等于达到业务吞吐。最终结论必须来自真实基准。

## 4. 初始化目录和安全配置

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Initialize-Runtime.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Initialize-Runtime.ps1
```

初始化脚本不会覆盖已有文件。它会生成目录安全标记、默认全 Mock 的 `config\runtime-agent.toml` 和含随机 Bearer Token 的 `config\runtime-agent.env`。不要授予 `Everyone:FullControl`；运行账户只需对运行时目录拥有必要的读写权限。

## 5. 安装独立 Runtime Agent

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Install-RuntimeAgent.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Install-RuntimeAgent.ps1
```

脚本使用 `runtime_agent/uv.lock` 冻结导出并同步精确依赖，再用 `--no-deps` 安装本地 Agent 包到 `components\runtime-agent\.venv`。缺少 `uv.lock` 或 uv 时会拒绝安装，不会退回到未锁定的 pip 求解。每次安装会在 `config` 写入源提交和锁文件 SHA-256 记录。

## 6. 分组件安装

`scripts/local-ai/components.example.json` 是全部禁用的安全模板。先复制到运行目录外的受控位置，并为每个固定组件填写：

- 经复核的许可证名称和 HTTPS 官方链接；
- 固定版本或提交，不允许 `latest`；
- 官方 HTTPS 直链、精确 `size_bytes` 和非零 SHA-256；
- 版本化目标目录，不使用可被静默覆盖的 `current` 目录。

不传组件 ID 时只列清单：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Install-RuntimeComponent.ps1 `
  -ManifestPath E:\AI\approved-components.json
```

逐个预演和执行：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Install-RuntimeComponent.ps1 `
  -ManifestPath E:\AI\approved-components.json `
  -ComponentId comfyui-windows-portable -DryRun

pwsh -NoProfile -File .\scripts\local-ai\Install-RuntimeComponent.ps1 `
  -ManifestPath E:\AI\approved-components.json `
  -ComponentId comfyui-windows-portable
```

脚本会显示许可证、下载大小、固定版本、安装模式、目标和缓存路径，并要求完整输入组件 ID。`archive` 模式只支持经过校验的 ZIP：先写 `.partial`、支持 Range 续传、核对大小与 SHA-256、解压到 `staging`，最后在同一运行根目录内原子切换到新的版本目录。已存在且不匹配的目录绝不覆盖。

`download_only` 用于 Ollama 等交互式官方安装器：脚本只校验并暂存安装包，不静默执行。执行前还需人工检查 Authenticode 发布者：

```powershell
Get-AuthenticodeSignature 'E:\AI\ai-story-runtime\cache\component-packages\ollama-windows-installer\OllamaSetup-PINNED.exe' |
  Format-List Status,StatusMessage,SignerCertificate
```

安装器、归档和源码是否适用于商业场景仍由操作者负责复核；模板不是法律结论。

## 7. 配置 Ollama

1. 从 Ollama 官方渠道获取 Windows 安装包并核对发布者。
2. 安装后先不下载多个模型。
3. 将模型目录指向运行时根目录（具体变量以当前 Ollama 版本文档为准）：

```powershell
[Environment]::SetEnvironmentVariable(
  'OLLAMA_MODELS',
  'E:\AI\ai-story-runtime\models\ollama',
  'User'
)
```

4. 重新打开 PowerShell，确认服务：

```powershell
Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

5. 不直接用浮动标签执行 `ollama pull`。模型必须进入下一节的批准清单或另有可审计的固定 digest 导入流程，并记录下载时间、摘要、磁盘占用和许可证。

## 8. 配置 ComfyUI、LightX2V 与 FFmpeg

1. 使用上一节安装的 ComfyUI 固定版本目录，不从 UI 自动安装未知自定义节点。
2. LightX2V 使用独立固定环境，不与 Django 或 Runtime Agent 虚拟环境混装。
3. FFmpeg 固定版本路径写入 Agent TOML，不依赖未来可能漂移的全局 `PATH`。
4. Ollama、ComfyUI、LightX2V 的命令与内部地址只写入 Agent 配置，不写入 Django API Key。
5. 每个自定义节点都固定仓库、提交、许可证和 SHA-256。

ComfyUI 额外要求：

1. 解压到 `E:\AI\ai-story-runtime\components\comfyui\<固定版本>` 或组织指定的只读程序目录。
2. 禁止默认安装未知自定义节点；每个节点应固定仓库、提交和许可证。
3. 将模型路径配置到运行时目录，避免在多个副本重复下载。
4. 以回环地址和 8188 端口启动，具体启动文件按安装包版本调整：

```powershell
Set-Location 'E:\AI\ai-story-runtime\components\comfyui\<固定版本>'
# 示例：实际参数以所安装版本为准
python main.py --listen 127.0.0.1 --port 8188
```

5. 验证服务，不提交真实生成任务：

```powershell
Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue
Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing
```

6. 将审查过的 workflow JSON 放入 `workflows` 对应目录，并记录版本与 SHA-256：

```powershell
Get-FileHash 'E:\AI\ai-story-runtime\workflows\image\*.json' -Algorithm SHA256
```

## 9. 逐包安装模型

`scripts/local-ai/manifest.example.json` 同样默认全部禁止下载。仅把 `download_enabled` 改为 `true` 不足以启用：脚本还会拒绝 `example.invalid`、零哈希、占位版本、未复核许可证、缺失精确字节数或模糊大小说明。

```powershell
# 不传 ID，只显示许可证、大小和目标清单
pwsh -NoProfile -File .\scripts\local-ai\Install-ModelPack.ps1 `
  -ManifestPath E:\AI\approved-models.json

# 无副作用预演单包
pwsh -NoProfile -File .\scripts\local-ai\Install-ModelPack.ps1 `
  -ManifestPath E:\AI\approved-models.json `
  -ModelId flux2-klein-4b -DryRun

# 真正执行仍需完整输入模型 ID
pwsh -NoProfile -File .\scripts\local-ai\Install-ModelPack.ps1 `
  -ManifestPath E:\AI\approved-models.json `
  -ModelId flux2-klein-4b
```

模型写入正式目标旁的 `.partial`；重复执行会用 HTTP Range 续传并验证服务器返回的 `Content-Range` 起点。大小和 SHA-256 全部通过后，才在同目录原子移动为正式文件。失败的 `.partial` 会保留；明确重下时使用 `-RestartPartial`，旧文件会改名而不是删除。成功后 `config\model-install.*.json` 会记录版本、哈希、目标和源清单哈希。

## 10. 启动 Runtime Agent（默认 Mock）

仓库 `runtime_agent/` 是独立 FastAPI 包，不继承根依赖，也不下载模型。首次安装依赖需要受控网络或已预热的 uv cache：

```powershell
Set-Location '<REPO_ROOT>'
pwsh -NoProfile -File .\scripts\local-ai\Start-LocalAI.ps1 -WaitUntilReady
pwsh -NoProfile -File .\scripts\local-ai\Test-Health.ps1 -RequireReady
```

默认监听 `127.0.0.1:9100`。另一个 PowerShell 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:9100/v1/health/live
$Headers = @{ Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>' }
Invoke-RestMethod http://127.0.0.1:9100/v1/health/ready -Headers $Headers
Invoke-RestMethod http://127.0.0.1:9100/v1/capabilities -Headers $Headers
```

能力清单中的 Mock adapter 不是 Ollama/ComfyUI/LightX2V 安装证明。

真实 adapter 的代码接线已存在，不需要修改 Python registry。完成组件、模型、workflow manifest 与基准后，人工编辑 `E:\AI\ai-story-runtime\config\runtime-agent.toml`：

1. 把 `[registry].mode` 从 `mock` 改为 `configured`。
2. 只把已验证的 `[adapters.<name>].enabled` 改为 true。
3. 用固定 digest/SHA-256 替换 `model_version` 占位值，填写精确 `model_ids` 和 `workflow_version`。
4. 保持 Ollama/ComfyUI 地址为回环地址；填写已存在的 manifest、模型目录、argv 和 FFmpeg 路径。
5. 调用 `POST /v1/runtime-reloads`；若返回 `409 RUNTIME_CONFIG_INVALID`，旧 registry 会继续运行，不要反复重启规避校验。

```powershell
$Headers = @{ Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>' }
Invoke-RestMethod http://127.0.0.1:9100/v1/runtime-reloads `
  -Method Post -Headers $Headers -ContentType 'application/json' `
  -Body '{"reason":"activate-pinned-adapter"}'
Invoke-RestMethod http://127.0.0.1:9100/v1/capabilities -Headers $Headers
```

配置加载成功只证明 adapter 可调用；真实 Provider 在基准通过前仍保持 `is_active=false`。

Django 当前变量：

```powershell
$env:AI_RUNTIME_ROOT = 'E:\AI\ai-story-runtime'
$env:AI_RUNTIME_AGENT_URL = 'http://127.0.0.1:9100'
$env:AI_RUNTIME_AGENT_TOKEN = $env:RUNTIME_AGENT_BEARER_TOKEN
$env:AI_ROUTER_V2_ENABLED = 'false'
$env:AI_ROUTER_V2_SHADOW_MODE = 'true'
```

`Start-LocalAI.ps1` 用隐藏窗口启动 Agent，并记录可验证的 PID、解释器路径和分离的 stdout/stderr 日志；它不会按进程名宽泛启动或停止第三方服务。真实 Adapter 在固定配置和基准通过前保持 `enabled=false`。LightX2V/FFmpeg 的任务取消会由 Agent 尝试终止进程树；Ollama/ComfyUI HTTP 调用仍受请求 timeout 边界约束，取消后需检查上游状态。

## 11. 防火墙与监听检查

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object LocalPort -in 9100,11434,8188 |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

预期 `LocalAddress` 为 `127.0.0.1` 或 `::1`。若为 `0.0.0.0`：

1. 立即停止服务；
2. 修正监听参数；
3. 检查 Windows 防火墙规则；
4. 按 [REMOTE_NODE_GUIDE.md](REMOTE_NODE_GUIDE.md) 设计受控远程节点，不要直接开放端口。

## 12. 分层验证

### 服务层

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing
Invoke-RestMethod http://127.0.0.1:9100/v1/health/live
$Headers = @{ Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>' }
Invoke-RestMethod http://127.0.0.1:9100/v1/health/ready -Headers $Headers
Invoke-RestMethod http://127.0.0.1:9100/v1/capabilities -Headers $Headers
```

### 模型层

- 文本：小提示、流式响应、JSON Schema、中文长文本；
- 图像：固定 seed、指定尺寸、参考图、产物可读；
- 视频：最短分镜、超时、取消、磁盘和显存回收。

### 项目层

1. 先用 Mock Provider；
2. 再建立禁用状态的本地 Provider；
3. 单项目、单阶段启用；
4. 记录 [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md)；
5. 未通过不得设为全局默认。

## 13. 启停、备份和日志

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Stop-LocalAI.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Stop-LocalAI.ps1
pwsh -NoProfile -File .\scripts\local-ai\Backup-Configuration.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Backup-Configuration.ps1
pwsh -NoProfile -File .\scripts\local-ai\Collect-Logs.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Collect-Logs.ps1
```

停止脚本只终止 PID 文件记录且解释器路径与当前 Runtime 完全匹配的 Agent。配置备份包含 `config`、`workflows`、安装记录和禁用示例清单，不包含模型、Agent journal、媒体或缓存。日志包默认脱敏，但发送前仍须人工复核。

## 14. 登录后隐藏启动与可选计划任务

当前推荐由登录脚本调用 `Start-LocalAI.ps1`，它会使用隐藏窗口启动。需要无人值守登录后启动时，可由管理员审查后创建“仅在用户登录时运行”的 Task Scheduler 任务；先输出 XML 或在测试账户演练，不把 Bearer Token 放入任务参数。

示例动作只引用脚本和固定根目录：

```powershell
$Pwsh = (Get-Command pwsh).Source
$Script = (Resolve-Path '.\scripts\local-ai\Start-LocalAI.ps1').Path
$Action = New-ScheduledTaskAction -Execute $Pwsh `
  -Argument "-NoProfile -File `"$Script`" -RuntimeRoot `"E:\AI\ai-story-runtime`" -WaitUntilReady"
$Trigger = New-ScheduledTaskTrigger -AtLogOn
# 审查 $Action / $Trigger 后，再由管理员显式调用 Register-ScheduledTask。
```

Windows Service 需要组织批准的 Service Wrapper、专用低权限账户、恢复策略和安全更新流程，本仓库不会自动安装第三方 wrapper。

## 15. 密钥处理

- 本地 Ollama/ComfyUI 不需要真实云 API Key时只使用不可复用的占位值；
- 当前数据库字段以明文存储 Provider Key，备份必须按敏感数据保护；
- 云密钥使用进程环境、凭据管理器或未来密钥服务注入；
- 禁止把密钥写入 `config/*.json`、workflow JSON、PowerShell 历史或 Git。

## 16. 升级、卸载与回滚

回滚不要求删除模型：

1. 停用本地 Provider；
2. 设置 `AI_ROUTER_V2_ENABLED=false`，并将项目付费/素材出站均关闭、项目预算与硬预算设为 `0`；
3. 停止 Runtime Agent、ComfyUI、Ollama；
4. 保留 `logs`、`data` 和配置快照；
5. 恢复原 Provider 绑定并执行单阶段烟测；
6. 只有备份验证后，才由管理员另行批准清理大模型文件。

升级前必须先执行 `Backup-Configuration.ps1`，保存当前组件/模型安装记录、workflow 和 TOML；新版本安装到新的版本目录。脚本不会覆盖旧版本，也不会自动修改 Agent 的固定版本配置。完成 Mock、健康和真实基准后再人工切换；失败时恢复旧 TOML/workflow 并重新启用旧版本目录。
