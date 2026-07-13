# 本地 AI 运行手册

> 状态说明：Runtime Agent 控制面、可配置真实 adapter、统一工作项、预算预留和本地优先路由已进入代码；默认配置仍为 Mock。只有目标环境测试通过的部分才能写“已验证”。真实 Ollama、ComfyUI、LightX2V 模型/工作流尚无本机基准证据，不能按生产推理可用处理。

## 1. 固定运行约定

| 项目 | 默认值 |
| --- | --- |
| 运行根目录 | `E:\AI\ai-story-runtime` |
| Runtime Agent | `http://127.0.0.1:9100` |
| Ollama | `http://127.0.0.1:11434` |
| ComfyUI | `http://127.0.0.1:8188` |
| 付费预算 | `0`，即默认禁止付费调用 |

本机服务默认只绑定回环地址。远程节点按 [REMOTE_NODE_GUIDE.md](REMOTE_NODE_GUIDE.md) 建立私网或 TLS 通道，不直接暴露端口。

## 2. 启动前检查

```powershell
$env:AI_RUNTIME_ROOT = 'E:\AI\ai-story-runtime'
pwsh -NoProfile -File .\scripts\local-ai\Test-Prerequisites.ps1
pwsh -NoProfile -File .\scripts\local-ai\Test-Health.ps1 -DryRun
```

同时确认：

- 数据库迁移已完成，Redis、Celery 与 Django 的环境配置一致。
- 模型许可证允许当前用途；模型文件、工作流和哈希与登记清单一致。
- `AI_ROUTER_V2_ENABLED=false`、项目付费/素材出站关闭、项目预算与硬预算 `0`，除非负责人明确批准切流、外发和付费。
- 数据库中的 Provider/Vendor 密钥和 RuntimeNode access token 是明文风险面；日志与检查输出不得打印。
- `config\runtime-agent-install.*.json`、`component-install.*.json`、`model-install.*.json` 与当前 TOML/workflow 哈希一致；不允许使用未登记的浮动版本。

## 3. 推荐启动顺序

1. Ollama `11434`。
2. ComfyUI `8188`。
3. Runtime Agent `9100`（仅在该组件已实际安装且通过健康检查时）。
4. Django、Celery worker、Celery beat。
5. 前端。

Runtime Agent 的实际命令：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Start-LocalAI.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Start-LocalAI.ps1 -WaitUntilReady
pwsh -NoProfile -File .\scripts\local-ai\Test-Health.ps1 -RequireReady -IncludeOllama -IncludeComfyUI
```

启动脚本只负责可验证归属的 Runtime Agent 进程。Ollama、ComfyUI 和 LightX2V 必须按固定组件配置启动；当前脚本不会按进程名宽泛拉起第三方程序。

示例检查，不代表启动命令一定适用于当前安装：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5
Invoke-WebRequest http://127.0.0.1:8188/ -UseBasicParsing -TimeoutSec 5
$Headers = @{ Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>' }
Invoke-RestMethod http://127.0.0.1:9100/v1/health/live -TimeoutSec 5
Invoke-RestMethod http://127.0.0.1:9100/v1/health/ready -Headers $Headers -TimeoutSec 5
```

`9100` 失败而 Django 仍配置为直连 Provider 时，可以继续验证现有直连路径；不要把 Agent 健康检查失败误判为所有推理均不可用。

## 4. 推荐停止顺序

1. 停止接收新工作项并将节点标记为 drain。
2. 等待运行中任务完成，或按工作项取消协议终止。
3. 停 Celery beat、worker 与 Django。
4. 停 Runtime Agent。
5. 停 ComfyUI 和 Ollama。

取消边界必须分开确认：LightX2V/FFmpeg CLI 会由 Agent 尝试终止整个进程树；Ollama 阻塞 HTTP 请求可能持续到 timeout，ComfyUI 已提交任务可能继续留在其队列。只有 Agent job、上游进程/队列、Django 工作项、产物和预算预留都核对完成后，才能停止服务或重试。

Runtime Agent 使用可审计停止脚本：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Stop-LocalAI.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Stop-LocalAI.ps1
```

脚本只有在 PID 记录、当前进程路径和 `components\runtime-agent\.venv\Scripts\python.exe` 完全匹配时才会停止；无法证明归属就拒绝操作。

不要直接删除队列、数据库行、输出目录或模型缓存。异常任务先记录工作项 ID、Provider、模型、工作流版本和最后状态。

## 5. 日常巡检

### 每次启动

```powershell
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object LocalPort -in 9100,11434,8188
nvidia-smi
Get-ChildItem 'E:\AI\ai-story-runtime\logs' -File -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,Length,LastWriteTime
```

检查健康状态、可用模型清单、磁盘剩余、GPU 显存、队列深度、失败率、预算余额、`ambiguous/manual_review` 预留和最近错误。健康接口不得包含密钥、完整提示词或用户原始素材。

### 每周

- 校验工作流文件哈希与备份。
- 抽查 Usage Log 是否与任务数量相符。
- 检查异常增长的日志、输出和临时文件；清理前先确认保留策略并备份。
- 复核本地模型许可证、远程节点访问控制和付费预算审批。

推荐先预演再生成配置备份和脱敏诊断包：

```powershell
pwsh -NoProfile -File .\scripts\local-ai\Backup-Configuration.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Backup-Configuration.ps1
pwsh -NoProfile -File .\scripts\local-ai\Collect-Logs.ps1 -WhatIf
pwsh -NoProfile -File .\scripts\local-ai\Collect-Logs.ps1
```

配置备份不包含模型、Agent journal 或媒体；这三类数据按 [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md) 的独立恢复演练处理。日志包虽执行模式脱敏，外发前仍必须人工复核。

## 6. 事件等级与响应

| 等级 | 示例 | 首要动作 |
| --- | --- | --- |
| P1 | 数据泄露、密钥暴露、失控付费 | 立即禁用付费回退，隔离节点，保留证据并轮换密钥 |
| P2 | 所有本地推理不可用、任务持续重复扣费 | 停止新任务，冻结预算，定位共享依赖 |
| P3 | 单模型/单工作流失败、显存不足 | 隔离路由，回退到已验证模型或人工重试 |
| P4 | 性能下降、非关键告警 | 记录基线差异，排期优化 |

## 7. 故障演练

每次演练都应记录时间、操作者、任务 ID、预期、实测和恢复时间；不得在真实用户任务上演练。

### Ollama 中断

1. 在测试环境停止 Ollama。
2. 提交本地文本测试任务。
3. 期望：健康状态降级；任务失败或进入有限重试；预算为 `0` 时不触发付费回退。
4. 恢复 Ollama，确认任务不会重复执行或重复计费。

### ComfyUI 中断

1. 停止 ComfyUI 或临时阻断 `8188`。
2. 提交测试工作流。
3. 期望：任务保留可诊断错误；不无限轮询 `/history`；恢复后仅按显式策略重试。

### Runtime Agent 中断

1. 停止 `9100`。
2. 期望：新目标架构请求快速失败；已有 Django 直连路径是否继续由配置决定。
3. 不得悄悄把请求发送到付费 Provider。

### 预算耗尽

将测试项目预算设为 `0`，请求一个仅有付费 Provider 的任务。期望在外部请求前失败，且没有 Usage Log 中的外部消费记录。

### 磁盘空间不足

使用测试卷或配额模拟，不填满系统盘。期望在提交生成任务前被预检拦截，已存在产物不被删除。

## 8. 紧急回滚

新路径出现问题时：

```powershell
$env:AI_ROUTER_V2_ENABLED='false'
$env:AI_ROUTER_V2_SHADOW_MODE='true'
$env:AI_ARTIFACT_CLEANUP_ENABLED='false'
```

同时设置 `allow_paid_fallback=false`、`allow_cloud_data_transfer=false`、`project_budget_cny=0` 和硬预算 `0`，停用 Runtime Agent Provider。关闭开关不是数据回滚：预算预留、运行中工作项和已提交外部请求仍需逐项处理。

组件或模型升级失败时不要覆盖旧目录：恢复备份中的 Agent TOML/workflow，重新指向上一份 `component-install` / `model-install` 记录所对应的固定版本，再运行 `Test-Health.ps1 -RequireReady` 和单项目 Mock/真实基准。未完成基准前不得重新开放该本地能力或付费回退。

## 9. 交接信息

事件交接至少包含：时间线、版本/提交、环境变量名称（不含值）、端口监听、模型与工作流版本、任务 ID、错误摘要、已执行动作、预算影响和下一动作。详细排障见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
