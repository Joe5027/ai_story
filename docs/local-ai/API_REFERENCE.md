# 本地 AI API 参考

> Runtime Agent v1 与 Django 混合推理控制面均已接线。Agent 在无 TOML 或 `[registry].mode="mock"` 时只运行确定性 Mock；将固定 TOML 设为 `configured` 后，可以显式加载通过完整校验且 `enabled=true` 的 Ollama、ComfyUI、LightX2V 或 FFmpegMotion adapter。API 可用不证明真实模型已安装、获得许可或通过硬件基准。实现状态见 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)。

## 1. 通用约定

- Base URL：`http://127.0.0.1:9100`。
- 非回环访问必须使用私网/TLS 和 Bearer token。
- 除 live 外的端点发送 `Authorization: Bearer <token>`。
- 创建任务必须发送 1 到 200 字符的 `Idempotency-Key`。
- 可发送 `X-Request-ID`；响应回显该 ID。
- 时间使用 UTC/RFC 3339，ID 为不透明字符串。
- 响应和日志不得包含 Provider 密钥、完整环境变量或任意文件内容。

错误 envelope：

```json
{
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "Idempotency-Key 已用于不同的请求体",
    "retryable": false,
    "details": {}
  },
  "request_id": "req_opaque"
}
```

## 2. 健康检查

### `GET /v1/health/live`

免鉴权，仅证明 HTTP 进程存活。

```json
{
  "status": "live",
  "service": "ai-story-runtime-agent",
  "version": "0.1.0"
}
```

### `GET /v1/health/ready`

需要鉴权。检查 SQLite journal、产物目录和调度线程；不加载真实模型，也不承诺模型质量。

```json
{
  "status": "ready",
  "checks": {
    "journal": true,
    "artifacts": true,
    "scheduler": true
  }
}
```

```powershell
$Base = 'http://127.0.0.1:9100'
$Headers = @{ Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>' }
Invoke-RestMethod "$Base/v1/health/live"
Invoke-RestMethod "$Base/v1/health/ready" -Headers $Headers
```

## 3. 能力

### `GET /v1/capabilities`

返回 `runtime_generation`、硬件快照、资源组容量和当前 registry 中的 adapters。默认资源组为 `gpu=1`、`cpu_motion=2`；默认 Mock 能力为：

- `llm`
- `text2image`
- `image_edit`
- `image2video`
- `motion_render`

`mode="configured"` 时，启用的真实 adapter 会替换同能力 Mock，未启用的能力继续保留 Mock。列表只表示当前进程加载的 adapter 契约；它不是许可证、质量、显存或生产可用证明。

## 4. 创建任务

### `POST /v1/jobs`

必须发送 `Idempotency-Key`。顶层请求契约与 Django Runtime Agent client 对齐：

```json
{
  "capability": "text2image",
  "model_id": "mock-image-v1",
  "profile": "draft",
  "prompt": "a safe test scene",
  "negative_prompt": "",
  "input_artifacts": [],
  "output_spec": {
    "width": 512,
    "height": 512
  },
  "seed": 42,
  "workflow_version": "mock-v1",
  "project_id": "project_opaque",
  "stage_type": "image_generation",
  "work_item_id": "work_opaque"
}
```

```powershell
$Headers = @{
  Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>'
  'Idempotency-Key' = 'local-doc-smoke-001'
  'X-Request-ID' = 'local-doc-request-001'
}
$Body = @{
  capability = 'llm'
  model_id = 'mock-llm-v1'
  profile = 'draft'
  prompt = 'return a short deterministic test result'
  negative_prompt = ''
  input_artifacts = @()
  output_spec = @{ max_tokens = 64 }
  seed = 42
  workflow_version = 'mock-v1'
  project_id = 'local-doc'
  stage_type = 'rewrite'
  work_item_id = 'local-doc-work-001'
} | ConvertTo-Json -Depth 8
Invoke-RestMethod "$Base/v1/jobs" -Method Post -Headers $Headers `
  -ContentType 'application/json' -Body $Body
```

首次创建返回 `202`；同 key、同规范化请求返回原任务且不重复执行；同 key、不同请求返回 `409 IDEMPOTENCY_CONFLICT`。

创建响应包含 `id`、`job_id`、`status` 和 `idempotent_replay`。任务查询在顶层增加能力、运行 generation、结果/错误、产物和关联字段。Django client 依赖顶层 `status`，不要再包成另一层未约定的 `job` 对象。

## 5. 查询与取消

### `GET /v1/jobs`

支持按 `status`、`capability` 和 `limit` 查询。最大 `limit` 为 500。

### `GET /v1/jobs/{job_id}`

返回一个任务的顶层当前快照。状态固定为：

```text
queued -> running -> succeeded | failed | cancelled
```

Agent 重启时，未请求取消的 `running` 任务按 journal 恢复策略处理；已请求取消的任务恢复为 `cancelled`。真实上游是否仍执行必须按 adapter 类型另行核对。

### `DELETE /v1/jobs/{job_id}`

固定返回 `202`。queued 任务立即取消，running 任务请求协作式取消。兼容别名 `POST /v1/jobs/{job_id}/cancel` 保留但不进入主 OpenAPI。

真实 adapter 的取消边界：

- LightX2V/FFmpeg：生产 CLI runner 会轮询 journal 取消标记并终止整棵进程树；Windows 使用 `taskkill /T` 后在宽限期结束时 `/F`，POSIX 使用独立进程组 TERM/KILL。
- Ollama：当前是单次 HTTP 请求，只保证 `timeout_seconds`；接受 Agent 取消不证明阻塞中的 HTTP 请求已经立刻停止。
- ComfyUI：history 轮询会检查取消并受总 timeout 限制，但当前未调用 ComfyUI queue delete/interrupt；取消后必须检查 ComfyUI 队列和显存。

因此 `202` 只表示 Agent 已接受取消意图，不能单独作为“上游已停止、无产物或无费用”的证据。

```powershell
Invoke-RestMethod "$Base/v1/jobs/<JOB_ID>" -Method Delete -Headers $Headers
```

## 6. 产物

### `GET /v1/jobs/{job_id}/artifacts`

列出任务产物元数据。

### `GET /v1/artifacts/{artifact_id}/metadata`

返回单个产物元数据和鉴权下载地址。

### `GET /v1/artifacts/{artifact_id}`

规范下载端点。下载前复核文件大小与 SHA-256，响应包含 `X-Artifact-SHA256`。`/download` 仅为兼容别名，不进入主 OpenAPI。

```powershell
Invoke-WebRequest "$Base/v1/artifacts/<ARTIFACT_ID>" `
  -Headers $Headers -OutFile '<APPROVED_OUTPUT_PATH>'
```

## 7. Runtime reload

### `POST /v1/runtime-reloads`

从 `RUNTIME_AGENT_CONFIG_PATH` 重新完整构建 adapter registry，并在全部校验成功后原子替换、持久化新的 generation。无配置或 `mode="mock"` 时结果仍为 Mock；`mode="configured"` 时可加载启用的真实 adapter。reload 不安装、下载或升级模型。

```json
{"reason":"manual validation"}
```

### `GET /v1/runtime-reloads`

列出 reload 记录，支持 `limit`。配置缺失、占位版本、URL/路径/manifest/命令不合法时返回 `409 RUNTIME_CONFIG_INVALID`，继续使用旧 generation，不能留下部分生效状态。

## 8. Django 与 Agent 的责任边界

以下能力位于 Django 推理领域，不属于 Agent HTTP API：

- 项目/业务阶段与用户权限。
- local-first 路由与付费回退资格。
- 价格匹配、成本估算和 AIBudgetPolicy。
- BudgetReservation 预留、结算、释放、结果不明和人工复核。
- GenerationWorkItem 的 `waiting/leased/running/retry_wait`、依赖、租约、心跳和版本控制。
- 云端数据出站授权与显式付费确认。

默认项目、每日和每月预算都为 `0.000000`；Agent 不持有 API Provider Key，也不能绕过 Django 预算直接选择付费 Provider。

## 9. Django 管理控制面

Django Base URL 为 `/api/v1`，以下接口均要求登录。DRF ViewSet 列表除 `budget-summary` 外沿用全局分页；分页响应使用 `count/next/previous/results`。

| 接口 | 方法 | 权限与行为 |
| --- | --- | --- |
| `/api/v1/models/runtime-nodes/` | GET/POST | 登录用户可读；创建仅管理员 |
| `/api/v1/models/runtime-nodes/{id}/` | GET/PATCH/DELETE | 写操作仅管理员；`access_token` 只写，读取仅有 `has_access_token`、`access_token_masked` |
| `/api/v1/models/runtime-nodes/{id}/refresh-health/` | POST | 管理员探测 Agent ready/capabilities 并更新快照 |
| `/api/v1/models/runtime-nodes/{id}/runtime-reload/` | POST | 管理员转发原子 reload；不下载模型 |
| `/api/v1/models/generation-profiles/` | GET | 只读稳定质量档配置 |
| `/api/v1/models/generation-routes/` | CRUD | 登录用户可读全局和自己项目路由；全局写仅管理员 |
| `/api/v1/models/generation-route-targets/` | CRUD | 目标写权限继承所属路由；API Provider 只能使用 `paid_fallback` 角色 |
| `/api/v1/models/provider-price-rates/` | CRUD | 登录用户可读；写仅管理员 |
| `/api/v1/models/budget-policies/` | CRUD | 登录用户可读；写仅管理员 |
| `/api/v1/models/budget-reservations/` | GET | 只读当前用户项目的预留 |
| `/api/v1/models/budget-reservations/{id}/resolve/` | POST | 人工把 `manual_review/ambiguous` 结算为 `charged` 或释放为 `not_charged` |
| `/api/v1/models/budget-summary/?project_id={id}` | GET | 返回单个摘要对象，不分页 |
| `/api/v1/models/generation-work-items/` | GET | 只读当前用户项目工作项，可按项目/能力/阶段/状态/Provider/节点/分镜筛选 |
| `/api/v1/models/generation-work-items/{id}/cancel/` | POST | 持久化取消并传播到 Agent/Celery，返回 `202` |
| `/api/v1/models/media-artifacts/` | GET | 只读当前用户项目产物元数据 |
| `/api/v1/models/usage-logs/` | GET | 调用账本；普通用户只见自己的项目，staff 可全局审计 |
| `/api/v1/models/usage-logs/export_csv/` | GET | 脱敏 CSV，不含 Key、完整提示词和媒体地址 |

RuntimeNode 的 `capabilities`、`hardware_snapshot`、`resource_groups`、健康和版本是最近一次探测快照，不是实时保证。`BudgetReservation.details`、`MediaArtifact.metadata` 和工作项错误字段在序列化时再次脱敏。

## 10. 项目 AI 设置与估算

### `GET|PUT|PATCH /api/v1/projects/projects/{project_id}/ai-settings/`

读取或更新：

- `default_profile_code`：`draft`、`balanced`、`final`。
- `prefer_local`。
- `allow_paid_fallback`。
- `allow_cloud_data_transfer`。
- `project_budget_cny`。
- `budget_policy`、默认/能力路由和参数覆盖。

项目第一次把 `allow_cloud_data_transfer` 从 false 改为 true 时，请求还必须包含：

```json
{
  "allow_cloud_data_transfer": true,
  "confirm_cloud_data_transfer": true
}
```

Django 记录当前授权人和时间。撤销出站授权会同时把 `allow_paid_fallback` 设为 false，并清空授权人/时间。

### `POST /api/v1/projects/projects/{project_id}/generation-estimates/`

请求示例：

```json
{
  "capability": "text2image",
  "stage_type": "image_generation",
  "profile": "balanced",
  "task_count": 3,
  "provider_id": "<可选的精确 Provider UUID>",
  "usage_per_item": {
    "request_count": 1,
    "image_count": 1,
    "width": 1024,
    "height": 1024
  }
}
```

不传 `provider_id` 时按路由估算最多两个付费 fallback 的最坏总成本；传入时只估算该精确 Provider，并把 `estimate_scope` 返回为 `explicit_provider`。视频还可传 `duration_seconds`、`native_max_seconds`、`fps`、`overlap_frames`，响应会返回 `segment_plan` 与扩展后的工作项数量。

关键响应字段：`worst_api_cost_cny`、`price_details`、`missing_configuration`、`automatic_fallback_allowed`、`manual_paid_execution_allowed`。只要缺云授权、价格、项目预算、全局日预算或全局月预算，允许标记就是 false；估算接口本身不提交外部生成请求。

## 11. 项目工作项操作

### `GET /api/v1/projects/projects/{project_id}/generation-work-items/`

按项目范围分页列出工作项，可用 `status`、`capability`、`stage_type` 过滤。响应不返回完整提示词或输入媒体，只返回 `request_summary`、脱敏后的可复现参数、状态/错误、成本和产物元数据。

### `POST /api/v1/projects/projects/{project_id}/retry-failed-items/`

Body 可选 `work_item_ids` 数组，只处理该项目中 `failed/retry_wait` 项。若存在尚未核对的付费结果，返回 `409 PAID_MANUAL_REVIEW_REQUIRED`，不会创建新请求。

### `POST /api/v1/projects/projects/{project_id}/regenerate-work-item-with-api/`

用于用户主观质量不满意后的单工作项 API 重生成。源工作项必须为 `succeeded` 或 `failed`，Provider 必须是能力匹配且启用的 `deployment_mode=api`。

请求头：

```text
Idempotency-Key: 1 到 200 字符的本次操作键
```

Body：

```json
{
  "work_item_id": "<终态工作项 UUID>",
  "provider_id": "<API Provider UUID>",
  "confirm_paid_generation": true,
  "confirmed_max_cost_cny": "0.250000"
}
```

接口先复核云授权、有效价目表和当前估算上限，再创建 `max_attempts=1` 的新工作项；真正执行前 PaidCallGate 会再次按项目/全局预算预留。相同源工作项、Provider 和 Idempotency-Key 返回同一工作项并标记 `idempotent_replay=true`。

## 12. 显式付费阶段执行

### `POST /api/v1/projects/projects/{project_id}/execute_stage/`

普通本地/旧路径仍可只传 `stage_name` 与 `input_data`。显式选择 API Provider 时必须先调用精确 Provider 估算，并满足：

- `AI_ROUTER_V2_ENABLED=true`。
- `Idempotency-Key` 请求头为 1–200 字符。
- 项目已有云端数据出站授权。
- 有效价目表覆盖阶段内每个付费工作项的用量。
- 项目预算、全局每日预算、全局每月预算均为正。
- 用户确认的总上限不低于事务内重新计算的阶段最坏成本。

示例：

```json
{
  "stage_name": "image_generation",
  "input_data": {
    "storyboard_ids": ["<storyboard UUID>"],
    "force_regenerate": true
  },
  "explicit_provider_id": "<API Provider UUID>",
  "manual_api": true,
  "confirm_paid_generation": true,
  "confirmed_max_cost_cny": "1.500000"
}
```

后端在同一数据库事务中创建完整阶段计划并累计付费项成本；任一费率缺失或总额超过确认上限会回滚整个计划。视频的最终 compose 工作项保持本地 `motion_render`。成功返回 `stage_execution_id`、`work_item_ids`、`paid_work_item_count`、`maximum_estimated_cost_cny` 和 `idempotent_replay`。

节点聊天和资产图片预览若选择 API Provider，也会先返回 `PAID_CONFIRMATION_REQUIRED`，其中带有 `provider_id/capability/stage_type/usage_per_item` 供前端估算；重试时必须提交确认位和 `confirmed_max_cost_cny`。

## 13. 完整流水线的付费安全限制

### `POST /api/v1/projects/projects/{project_id}/run_pipeline/`

当前接口不能可靠聚合完整流水线中旧 Provider 配置的最大总成本。因此，只要项目模型配置或启用的提示词模板直接绑定 API Provider，API 返回：

```json
{
  "error": {
    "code": "PAID_PIPELINE_CONFIRMATION_REQUIRED",
    "message": "完整流水线包含直接 API Provider，当前无法可靠聚合最大费用；请逐阶段确认执行。",
    "providers": []
  }
}
```

HTTP 状态为 `409`，不会排队。Celery worker 在真正执行完整流水线前还会重新检查一次，防止任务排队后配置漂移。当前安全操作是逐阶段调用估算和 `execute_stage/`；不存在可绕过的“确认整条流水线”参数。

## 14. 上游接口边界

真实 adapter 已可由固定 TOML 接线，但仓库默认关闭，模型/进程是否安装仍必须现场确认：

- Ollama：OpenAI-compatible `POST /v1/chat/completions`；模型 ID 和 workflow 版本必须在固定白名单中。
- ComfyUI：`POST /prompt`、`GET /history/{prompt_id}`、`GET /view`；workflow manifest 在 reload 时验证版本、模型和绑定。
- LightX2V：受控 argv 模板，`shell=False`；以锁定版本的程序和模型目录为准。
- FFmpegMotion：受控 zoom/pan/crossfade/精确裁剪，`shell=False`。

Ollama/ComfyUI URL 在 Agent 配置中只能指向回环地址。ComfyUI 不接受客户端任意 workflow 或任意本机路径。

## 15. 验收

- 健康、鉴权和非回环策略测试。
- Mock 与配置化真实 adapter registry/reload 契约测试。
- 幂等重放/冲突、取消、容量和 SQLite 重启恢复测试。
- CLI 取消/超时进程树清理，以及 HTTP adapter timeout/上游核对演练。
- 产物 SHA-256、大小复核和鉴权下载测试。
- Django 权限、出站授权、价格、预算、工作项和脱敏接口测试。
- 精确估算、单工作项 API 重生成、显式阶段确认与完整流水线阻断测试。
- 激活真实 adapter 后另跑模型、硬件、PostgreSQL/Redis 和失败恢复演练；Mock/SQLite 测试不能替代。
