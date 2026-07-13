# AI Story Windows 本地运行时脚本

这组脚本用于在 Windows 上分层准备和运维 AI Story Runtime Agent。默认根目录为 `E:\AI\ai-story-runtime`，可通过每个脚本的 `-RuntimeRoot` 或主环境变量 `AI_RUNTIME_ROOT` 覆盖；`AI_STORY_RUNTIME_ROOT` 仅作为旧脚本兼容回退。

脚本默认遵循四条安全边界：

1. 不默认安装或下载 Ollama、ComfyUI、LightX2V、模型包。
2. 不覆盖已有配置、模型或用户数据，也不使用宽泛进程名终止服务。
3. 所有变更型脚本支持 PowerShell 原生 `-WhatIf` 和无副作用 `-DryRun`。
4. 模型包必须逐包展示许可证、大小和目标路径，并由操作者完整输入模型 ID 确认。

## 环境要求

- Windows 10/11。
- PowerShell 7 或更高版本。
- Python 3.11 或更高版本，仅用于独立 Runtime Agent 虚拟环境。
- uv，用于从 `runtime_agent/uv.lock` 冻结同步独立依赖；脚本不会退回未锁定的 pip 求解。
- Git 为安装本仓库 Runtime Agent 的基础依赖。
- FFmpeg、NVIDIA 驱动、Ollama、ComfyUI、LightX2V 按质量档和适配器需要单独安装；预检只报告，不会安装。

先执行只读预检：

```powershell
pwsh -File .\scripts\local-ai\Test-Prerequisites.ps1
```

使用其他磁盘：

```powershell
$env:AI_RUNTIME_ROOT = 'D:\AI\ai-story-runtime'
pwsh -File .\scripts\local-ai\Test-Prerequisites.ps1 -Strict
```

## 推荐执行顺序

### 1. 预览并初始化目录

```powershell
pwsh -File .\scripts\local-ai\Initialize-Runtime.ps1 -WhatIf
pwsh -File .\scripts\local-ai\Initialize-Runtime.ps1
```

初始化只会创建 `models`、`workflows`、`cache`、`data`、`staging`、`logs`、`config`、`run`、`backups`、`components` 和 `artifacts`，并在配置不存在时生成本地 Bearer Token。已有文件保持不变。

### 2. 安装独立 Runtime Agent

```powershell
pwsh -File .\scripts\local-ai\Install-RuntimeAgent.ps1 -WhatIf
pwsh -File .\scripts\local-ai\Install-RuntimeAgent.ps1
```

脚本在 `components\runtime-agent\.venv` 创建独立虚拟环境，从 `runtime_agent/uv.lock` 冻结导出并同步精确依赖，再以 `--no-deps` 安装当前仓库的 Agent 包。它不会升级系统 Python，也不会安装其余推理运行时或模型。

### 3. 从批准清单分组件安装

仓库 `components.example.json` 默认全部禁用，只提供 Ollama、ComfyUI、LightX2V 和 FFmpeg 的清单结构。准备好经过批准的固定版本、许可证、HTTPS URL、精确大小和 SHA-256 后：

```powershell
pwsh -File .\scripts\local-ai\Install-RuntimeComponent.ps1 -ManifestPath E:\AI\approved-components.json
pwsh -File .\scripts\local-ai\Install-RuntimeComponent.ps1 -ManifestPath E:\AI\approved-components.json -ComponentId comfyui-windows-portable -DryRun
pwsh -File .\scripts\local-ai\Install-RuntimeComponent.ps1 -ManifestPath E:\AI\approved-components.json -ComponentId comfyui-windows-portable
```

`archive` 模式把校验通过的 ZIP 解压到临时目录，再原子切换到新的版本目录；`download_only` 只校验并暂存 Ollama 等交互式安装器，绝不静默执行。两种模式都要求完整输入组件 ID，不覆盖已存在版本。

### 4. 准备并逐包下载模型

仓库内的 `manifest.example.json` 仅是禁用下载的安全模板。不要直接把 `download_enabled` 改成 `true`；先从模型官方发布页完成以下工作：

- 选择允许当前使用场景的固定 revision/digest。
- 保存许可证名称和官方链接。
- 填写 HTTPS 直链、精确字节数、SHA-256 和带版本的目标路径。
- 对 ComfyUI workflow 和自定义节点另行固定版本。

不传模型 ID 时仅列清单：

```powershell
pwsh -File .\scripts\local-ai\Install-ModelPack.ps1
```

预演指定模型包：

```powershell
pwsh -File .\scripts\local-ai\Install-ModelPack.ps1 -ManifestPath E:\AI\approved-models.json -ModelId flux2-klein-4b -DryRun
```

真正执行时仍需完整输入模型 ID。下载写入目标旁的 `.partial`，再次执行会用 HTTP Range 尝试续传并验证 `Content-Range` 起点；服务器不支持 Range 时只会重写 `.partial`。只有精确大小和 SHA-256 均通过后才用同目录 `Move-Item` 原子切换为正式文件。占位 URL、零哈希、模糊大小、未复核许可证和浮动版本都会被拒绝。

哈希失败的 `.partial` 会保留。确认需要从头下载时使用 `-RestartPartial`，旧文件会改名为 `.invalid.<时间>`，不会删除。

### 5. 启动、检查和停止

```powershell
pwsh -File .\scripts\local-ai\Start-LocalAI.ps1 -WaitUntilReady
pwsh -File .\scripts\local-ai\Test-Health.ps1 -RequireReady
pwsh -File .\scripts\local-ai\Test-Health.ps1 -IncludeOllama -IncludeComfyUI
pwsh -File .\scripts\local-ai\Stop-LocalAI.ps1
```

启动脚本只启动独立 Runtime Agent，使用隐藏窗口和带时间戳的标准输出/错误日志。Ollama、ComfyUI 和 LightX2V 必须由固定版本的 Agent 适配配置管理，不会被脚本按进程名批量启动或停止。停止脚本同时校验 PID 记录和虚拟环境 Python 路径，无法确认归属时会拒绝停止。

本机默认允许回环健康检查；远程 Agent URL 必须使用 HTTPS。远程节点还应配置防火墙、TLS 和独立 Bearer Token。

## 配置备份与日志收集

```powershell
pwsh -File .\scripts\local-ai\Backup-Configuration.ps1 -WhatIf
pwsh -File .\scripts\local-ai\Backup-Configuration.ps1
pwsh -File .\scripts\local-ai\Collect-Logs.ps1
```

配置备份使用新的时间戳目录，不覆盖旧备份；包含 `config`、`workflows` 和安装清单，不包含模型、缓存、SQLite journal、媒体或临时产物。Bearer Token 默认替换为 `<redacted>`；只有明确使用 `-IncludeSecrets` 才会备份明文令牌，之后必须按敏感数据保护。

日志收集仅截取文本日志末尾并按常见 Token/Key/Password 模式脱敏，同时生成 GPU、磁盘、端口和相关进程的只读诊断。发送日志包前仍须人工复核。它不包含配置文件、数据库、提示词或媒体。

## 验证

无需 Pester：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Test-LocalAIScripts.ps1
```

验证包括：

- 全部 `.ps1` / `.psm1` 通过 PowerShell AST 语法解析。
- 组件和模型下载具备 `.partial`、SHA-256、逐包确认和原子移动契约。
- 示例清单没有任何默认启用的组件或模型下载。
- 所有变更型脚本都声明 `SupportsShouldProcess` 和 `DryRun`。
- 对临时路径执行整套 `-DryRun` / `-WhatIf`，并确认未创建目录。

## 明确不做的事

- 不执行模型或 ComfyUI workflow 自动升级。
- 不猜测本地 URL 就把 Provider 当成本地模型。
- 不把 API Provider Key 写入 Runtime Agent。
- 不清理模型、媒体、数据库、失败产物或日志。
- 不把模型许可证模板视为法律结论；启用前必须复核所选固定版本。
