# 本地 AI 排障手册

> 先保留证据，再恢复服务。不要用删除数据库行、清空队列、递归删除运行目录或反复提交任务来“试一下”。`9100` Runtime Agent 默认是 Mock，但也可能已从固定 TOML 加载真实 adapter；必须用 `/v1/capabilities`、TOML generation 和目标服务状态共同确认。

## 1. 快速分诊

```text
请求失败
├─ Django/API 不可达 -> 检查应用进程、数据库、Redis
├─ 文本任务失败 -> 检查 11434、模型清单、OpenAI 兼容路径、JSON 输出
├─ 图像/视频失败 -> 检查 8188、工作流 JSON、模型/custom nodes、显存和队列
├─ 9100 失败 -> 判断是否启用了目标 Runtime Agent；检查 Agent 与上游
├─ 付费回退被拒 -> 检查预算、数据分类、显式开关和预留状态
└─ 结果重复/卡住 -> 检查幂等键、工作项状态、Celery 重试和外部请求 ID
```

## 2. 采集基础证据

```powershell
Get-Date -Format o
git rev-parse HEAD
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object LocalPort -in 9100,11434,8188 |
  Select-Object LocalAddress,LocalPort,OwningProcess
Get-Process -ErrorAction SilentlyContinue |
  Where-Object Id -in (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue).OwningProcess |
  Select-Object Id,ProcessName,Path
nvidia-smi
Get-PSDrive -Name C,E | Select-Object Name,Free,Used
```

分享输出前删除用户名、素材路径、提示词、请求头、环境变量值和密钥。

## 3. 端口不可达

### Ollama `11434`

```powershell
Test-NetConnection 127.0.0.1 -Port 11434
Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5
```

- 未监听：确认 Ollama 是否实际安装并启动；候选模型清单不代表模型存在。
- 返回空模型：安装步骤尚未完成或模型目录与服务账户不一致。
- 连接成功但兼容调用 404：核对 base URL 和 OpenAI 兼容端点路径。

### ComfyUI `8188`

```powershell
Test-NetConnection 127.0.0.1 -Port 8188
Invoke-WebRequest http://127.0.0.1:8188/ -UseBasicParsing -TimeoutSec 5
```

- UI 可开但任务失败：查看 ComfyUI 控制台、`/history/{prompt_id}` 和节点错误。
- `node not found`：custom node 未安装或版本漂移；按锁定清单恢复，不盲目更新全部节点。
- 模型找不到：核对文件名、目录、配置和哈希。

### Runtime Agent `9100`

```powershell
Test-NetConnection 127.0.0.1 -Port 9100
$Headers = @{ Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>' }
Invoke-RestMethod http://127.0.0.1:9100/v1/health/live -TimeoutSec 5
Invoke-RestMethod http://127.0.0.1:9100/v1/health/ready -Headers $Headers -TimeoutSec 5
```

若未启动仓库 `runtime_agent/` 包，结论是“Agent 未部署”。若 capabilities 只列 Mock，结论是“控制链可测、真实模型未激活”。若列出真实 adapter，只能证明 TOML 已加载；还要核对模型/manifest/进程和基准。设置 `AI_ROUTER_V2_ENABLED=false` 并停用 Agent Provider 后可验证既有直连回滚路径。

### 取消后任务仍占资源

1. 从 Django 工作项读取 `agent_job_id`，再查询 Agent job 状态，不要只看前端提示。
2. LightX2V/FFmpeg：确认进程树已退出并复查 `nvidia-smi`/CPU；若仍残留，保存日志后按固定 PID 处理，不按名称批量杀进程。
3. Ollama：等待本次 HTTP `timeout_seconds` 边界并观察服务请求；Agent `202` 不保证阻塞请求已经中断。
4. ComfyUI：检查 `/history/{prompt_id}`、队列和显存；当前 Agent 不主动调用 queue delete/interrupt。
5. 外部付费调用或结果是否明确未知时，将预算预留保留在 `manual_review`，禁止释放后直接重试。

## 4. 文本任务问题

### JSON 解析失败

- 保存原始响应的脱敏摘要和模型参数。
- 确认提示词要求严格 JSON，无 Markdown 包裹。
- 校验 schema、长度截断和 stop 参数。
- 有限次数修复；不要把同一失败无限提交给付费模型。

### 输出质量不稳定

固定模型版本、量化、temperature、seed（若支持）和提示词版本，用基准集复现。单次重新生成成功不能算修复。

## 5. ComfyUI 任务问题

### 工作流 JSON 无效

当前适配器要求 `prompt` 是完整工作流 JSON。先在隔离环境解析：

```powershell
Get-Content 'E:\AI\ai-story-runtime\workflows\story-image-v1.json' -Raw |
  ConvertFrom-Json | Out-Null
```

然后核对工作流哈希与登记版本。不要把普通提示词文本直接放入该字段。

### 队列长时间不动

检查 ComfyUI 队列、GPU 使用、模型加载、磁盘和历史接口。先停止新任务，再判断运行中任务是否仍推进；不要同时重启多个组件导致证据丢失。

### CUDA OOM

记录工作流、分辨率、帧数、batch、模型和峰值显存。降低经过批准的资源参数或路由到更大节点；不得自动无限降质。恢复后用同一失败样例验证。

## 6. 预算或付费回退问题

预算 `0` 导致拒绝是预期安全行为。若本地 Provider 不可用且付费回退关闭，系统应明确失败，不应静默外发。

发现异常付费时立即：

```powershell
$env:AI_ROUTER_V2_ENABLED='false'
$env:AI_ROUTER_V2_SHADOW_MODE='true'
```

随后停止新任务、保存外部请求 ID 和 Usage Log、禁用受影响 Provider，并评估是否轮换密钥。

## 7. 重复任务与状态卡死

检查：

- API 幂等键是否一致。
- Celery `autoretry`、超时和 worker 重启记录。
- 外部 Provider 是否已接受请求但本地超时。
- 工作项最后状态、版本号、lease 和心跳。
- 预算预留是否为 active/settled/released/expired；ambiguous/manual_review 必须冻结并人工对账。

工作项状态机详见 [../adr/0004-work-item-state-machine.md](../adr/0004-work-item-state-machine.md)。它已在领域服务实现；若迁移或任务接线未验证，仍需同时用 ProjectStage/Celery/Agent journal 证据定位。

## 8. 安全恢复

1. 冻结新流量或仅隔离故障 Provider。
2. 保存时间线、配置名称、版本、任务/请求 ID 和脱敏日志。
3. 恢复一个依赖并执行一个已知测试样例。
4. 确认无重复执行、无意外付费、无数据外发。
5. 逐步恢复流量，持续观察。

## 9. 升级排障包

提交给维护者的信息：

- 预期与实际结果、首次发生时间、复现率。
- Git 提交、Python/Node/驱动/ComfyUI/Ollama 版本。
- 模型与工作流版本和哈希。
- 端口监听和健康状态。
- 脱敏错误堆栈、任务 ID、外部请求 ID。
- 已执行动作及其结果。
- 是否涉及个人数据、密钥或付费。

不要包含真实密钥、完整用户素材或未经脱敏的数据库。
