# 本地 AI 实现状态

> 最后仓库验证：2026-07-13。本文描述当前源码具备的能力与默认安全状态；本次已在一次性 SQLite、PostgreSQL 16 和 Redis 7 环境完成下述控制面验证，但仍不是目标机器真实模型验收报告。Qwen、FLUX、LightX2V/Wan、GPU 负载和跨进程恢复仍须按本文末尾步骤验证。

## 1. 状态词约定

| 状态 | 含义 |
| --- | --- |
| 代码已实现 | 当前仓库存在对应模型、服务、接口或脚本，并已完成本文列出的仓库级验证；不等于实机模型可用 |
| 默认关闭 | 安全模板或环境默认不会激活该能力 |
| 需人工激活 | 必须填写固定版本、路径、摘要或预算，并由操作者显式开启 |
| 实机未验证 | 本仓库没有足够证据证明目标机器上的质量、容量或故障恢复已达标 |

Mock API、离线单元测试和静态脚本检查只能证明控制契约，不能把“实机未验证”提升为“可用于生产”。

## 2. 当前代码状态

| 范围 | 当前状态 | 源码/配置入口 | 仍需完成 |
| --- | --- | --- | --- |
| Runtime Agent v1 | 代码已实现；无 TOML 时为 Mock；39 项契约测试与 core smoke 已通过 | `runtime_agent/runtime_agent/main.py`、`journal.py`、`scheduler.py` | 在目标机安装 Agent，执行真实子进程/重启/产物演练 |
| Adapter registry | 代码已实现；模板默认 `mode="mock"` | `runtime_agent/runtime_agent/registry.py`、`runtime_agent/config.example.toml` | 复制 TOML、固定版本与白名单，再显式改为 `configured` |
| Ollama LLM adapter | 可通过 TOML 激活；默认关闭 | `[adapters.ollama]`、`real_adapters.py` | 安装固定模型、填 digest、跑结构化文本基准 |
| ComfyUI 文生图/编辑 adapter | 可通过 TOML 分能力激活；默认关闭 | `[adapters.comfyui_text2image]`、`[adapters.comfyui_image_edit]` | 安装固定 ComfyUI/custom nodes，提交版本化 manifest 并跑图像基准 |
| LightX2V adapter | 可通过 TOML 激活；默认关闭 | `[adapters.lightx2v]`、`real_adapters.py` | 固定 Python 环境、模型目录和 argv，跑显存/时延/连续性基准 |
| FFmpeg motion adapter | 可通过 TOML 激活；默认关闭 | `[adapters.ffmpeg_motion]`、`real_adapters.py` | 固定 FFmpeg 可执行文件版本，跑 720p/24fps/8–10 秒样例 |
| CLI 取消与超时 | 代码已实现进程树清理 | `runtime_agent/runtime_agent/process_supervisor.py` | 在目标 Windows 节点演练 LightX2V/FFmpeg 取消和超时后的 GPU/CPU 释放 |
| HTTP adapter 取消 | 只具备请求 timeout/轮询取消边界 | `real_adapters.py` 中 Ollama/ComfyUI transport | 取消后核对上游实际任务；当前不能保证 HTTP 请求或 ComfyUI 队列已被主动中断 |
| Windows 分层安装 | 代码已实现；示例下载全部关闭；12 个脚本的 AST/安全/DryRun/WhatIf 契约已通过 | `scripts/local-ai/*.ps1`、`components.example.json`、`manifest.example.json` | 制作已审批清单并逐组件/模型确认；脚本不会自动安装真实模型 |
| Django 推理领域 | 代码已实现、含迁移；SQLite 与 PostgreSQL 16 定向套件已通过 | `backend/apps/inference/`、`backend/apps/models/` | 使用真实业务副本前仍需按迁移手册备份和核对表计数 |
| 路由/价格/预算三重门 | 代码已实现；路由 V2 默认关闭、预算默认 0 | `services/routing.py`、`pricing.py`、`gates.py` | 配置 canary 项目、有效价目表、项目及全局日/月预算 |
| 可恢复工作项/产物 | 代码已实现；PostgreSQL 20 worker 单项竞争仅 1 个领取成功 | `services/stage_planner.py`、`scheduler.py`、`projection.py`、`models.py` | 继续做 Celery、Agent、Redis 分别重启后的跨进程恢复演练 |
| Django 控制面 API | 代码已实现 | `/api/v1/models/*`、`/api/v1/projects/projects/{id}/*` | 以权限不同的用户跑 API/浏览器测试 |
| 显式 API 重生成 | 代码已实现；需估算、确认上限和幂等键 | `regenerate-work-item-with-api/`、`execute_stage/` | 只在测试项目用小额预算验证一次；不得用生产素材试错 |
| 完整流水线直连 API | 当前安全阻断 | `backend/apps/projects/paid_safety.py`、`run_pipeline/` | 含直连 API Provider 时改为逐阶段估算与确认；当前没有全流水线总价确认入口 |
| 本地 AI 前端控制面 | 代码已实现；lint、build 与 authenticated smoke 已通过 | `frontend/src/views/local-ai/`、`frontend/src/services/paidGenerationGuard.js` | 在 canary 项目用零预算/小额测试价目表演练人工 API 确认 |

## 3. Adapter 激活事实

真实 adapter 不是“尚未接线”。当前激活条件是：

1. `RUNTIME_AGENT_CONFIG_PATH` 指向可读 TOML；显式指定但文件不存在或格式错误时 Agent 拒绝启动。
2. `schema_version = 1`。
3. `[registry].mode = "configured"`。
4. 目标 `[adapters.<name>].enabled = true`。
5. `model_ids` 为精确非空白名单，`model_version` 与 `workflow_version` 已替换占位值。
6. ComfyUI manifest、LightX2V 模型目录/命令或 FFmpeg 可执行文件通过 reload 前校验。
7. `POST /v1/runtime-reloads` 成功后才原子替换 registry；失败保留上一 generation。

未配置某一真实能力时，该能力仍保留 Mock adapter。`GET /v1/capabilities` 返回的是当前 registry，不是模型质量或许可证证明。

## 4. 取消边界

- Mock：调度循环协作式检查取消标记。
- LightX2V/FFmpeg：默认生产 runner 使用 `subprocess.Popen(shell=False)`；Windows 通过 `taskkill /PID <pid> /T`，超出宽限期后 `/F`，POSIX 使用独立进程组的 TERM/KILL。
- Ollama：单次 HTTP 请求受 `timeout_seconds` 限制；Agent 接受取消不代表阻塞中的 HTTP 调用已经立刻终止。
- ComfyUI：提交后的 history 轮询会检查取消并受总 timeout 限制，但当前 Agent 未调用 ComfyUI queue delete/interrupt；取消后必须在 ComfyUI 侧确认任务是否仍运行。
- 任何“已接受取消”都不允许直接推断产物不存在或计费为零；Django 工作项、Agent job、上游任务和预算预留必须分别核对。

## 5. 付费入口状态

自动付费回退只有在本地出现允许的技术/契约失败后才可进入，并按顺序检查：

1. 项目云端数据出站授权及授权人/时间；
2. 当前 Provider、模型、能力和用量存在有效版本化价目表；
3. 项目预算、全局每日预算和全局每月预算均为正且可原子预留；
4. 项目已开启 `allow_paid_fallback`。

人工 API 重生成不要求把主观不满意伪装成技术失败，也不要求开启自动回退，但仍必须满足前 3 项，并提供本次费用确认、确认上限和 1–200 字符幂等键。当前支持：

- 终态工作项：`POST /api/v1/projects/projects/{id}/regenerate-work-item-with-api/`。
- 整个阶段：`POST /api/v1/projects/projects/{id}/execute_stage/`，且 `AI_ROUTER_V2_ENABLED=true`。
- 完整流水线：若旧项目配置或提示词模板直接绑定 API Provider，`run_pipeline/` 返回 `409 PAID_PIPELINE_CONFIRMATION_REQUIRED`；请逐阶段执行。

## 6. 当前不可宣称完成的验收

截至本文核对时，以下项目没有可写成“已通过”的仓库证据：

- Qwen、FLUX、LightX2V/Wan 在当前 RTX 3080 Laptop 上的真实耗时、显存、内存和质量。
- 720p、24fps、8–10 秒真实视频连续生成达到用户可接受质量。
- 过夜连续任务和真实模型切换/卸载稳定性。
- Redis 资源租约与 Celery/Agent 重启恢复；本次只证明真实 Pub/Sub 和 Daphne/EventSource/Vue 成功/失败流。
- 远程 Windows/Linux GPU 节点 TLS、鉴权和容量演练。

真实结果只应写入 [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md) 或本次交付验证记录，不得从 Mock 结果推导。

## 7. 本次仓库级验证证据

| 范围 | 结果 |
| --- | --- |
| SQLite 后端目标套件 | 161 项通过，3 项因要求 PostgreSQL 行锁而跳过 |
| PostgreSQL 16 后端目标套件 | 161 项全部通过；包含 20 并发预算上限和 20 worker 单项领取 |
| PostgreSQL 迁移/检查 | 一次性数据库完整迁移成功，`manage.py check` 通过 |
| Redis 流式门禁 | SSE/Celery 8 项、真实 Pub/Sub 1 项、Daphne/EventSource/Vue 成功与失败流程全部通过 |
| Runtime Agent | 39 项 pytest 通过，`scripts/core_smoke.py` 通过；仅有上游 Starlette TestClient 弃用告警 |
| Windows 脚本 | 12 个脚本的语法、安全、DryRun/WhatIf 契约通过；没有下载或安装真实组件/模型 |
| 仓库总门禁 | AI harness、Django check、迁移漂移、前端 audit/lint/build、authenticated smoke 均通过 |

上述 PostgreSQL/Redis 容器均为一次性本地容器，验证结束后已删除；镜像缓存保留。测试使用虚构账号、虚构价格和 Mock Provider，没有真实 API Key、用户素材或付费请求。

## 8. 建议复验命令

```powershell
# Runtime Agent 契约；不调用真实模型
Set-Location .\runtime_agent
uv sync --frozen --extra test
uv run --extra test pytest

# Django 定向检查
Set-Location ..\backend
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test apps.inference.tests apps.models.tests apps.projects.tests apps.ai_proxy.tests

# Windows 脚本安全契约
Set-Location ..
pwsh -NoProfile -File .\scripts\local-ai\Test-LocalAIScripts.ps1

# 仓库与前端门禁
node .\scripts\validate-ai-harness.cjs
node .\scripts\validate-local.cjs
```

以下命令用于在新的、一次性 PostgreSQL/Redis 环境复现生产并发/流式证据：

```powershell
# 先把 DATABASE_URL 指向非生产 PostgreSQL，再执行 migrate 和并发定向测试。
uv run python .\backend\manage.py migrate --noinput
node .\scripts\validate-streaming-local.cjs --require-redis
```

不要将本机生产库、真实 API Key 或真实用户素材用于验收。
