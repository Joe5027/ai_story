# 本地 AI 配置指南

> 状态：本文只把仓库实际读取的变量写成“已实现”。推理领域模型、路由/预算/工作项服务与配置化 Runtime Agent adapter 已进入代码；默认配置仍为 Mock。真实 Ollama、ComfyUI、LightX2V 模型与工作流需用户显式安装、固定版本并实测。

## 1. 配置优先级

1. 进程环境变量和部署系统注入的密钥。
2. Django 数据库中的项目、路由、Provider、运行节点和预算策略。
3. Runtime Agent 自己的环境变量。
4. 代码中的安全默认值。

工作流 JSON、提示词或请求参数不得覆盖预算、监听地址、允许的 Provider、鉴权和输出根目录。

## 2. Django 已实现变量

| 变量 | 默认值 | 敏感 | 说明 |
| --- | --- | --- | --- |
| `AI_ROUTER_V2_ENABLED` | `false` | 否 | 新路由主开关；默认不切流 |
| `AI_ROUTER_V2_SHADOW_MODE` | `true` | 否 | 只对比新旧决策，不应改变实际执行 |
| `AI_RUNTIME_ROOT` | `E:\AI\ai-story-runtime` | 否 | 固定运行根目录 |
| `AI_RUNTIME_AGENT_URL` | `http://127.0.0.1:9100` | 否 | Runtime Agent 地址 |
| `AI_RUNTIME_AGENT_TOKEN` | 空 | 是 | Django 调用 Agent 的 Bearer token |
| `AI_INTERMEDIATE_RETENTION_DAYS` | `30` | 否 | 中间产物保留天数 |
| `AI_ARTIFACT_CLEANUP_ENABLED` | `false` | 否 | 自动清理开关；默认关闭 |

`OLLAMA_BASE_URL`、`COMFYUI_BASE_URL` 不是当前 Django settings 的全局变量；它们应作为 ModelProvider 或 RuntimeNode 的地址登记，默认分别使用 `http://127.0.0.1:11434` 和 `http://127.0.0.1:8188`。

当前 PowerShell 安全基线：

```powershell
$env:AI_ROUTER_V2_ENABLED = 'false'
$env:AI_ROUTER_V2_SHADOW_MODE = 'true'
$env:AI_RUNTIME_ROOT = 'E:\AI\ai-story-runtime'
$env:AI_RUNTIME_AGENT_URL = 'http://127.0.0.1:9100'
$env:AI_RUNTIME_AGENT_TOKEN = '<与 Agent 一致的长随机令牌>'
$env:AI_INTERMEDIATE_RETENTION_DAYS = '30'
$env:AI_ARTIFACT_CLEANUP_ENABLED = 'false'
```

不要用 `Get-ChildItem Env:` 全量输出作为问题附件。

## 3. Runtime Agent 已实现变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RUNTIME_AGENT_BEARER_TOKEN` | `change-me-in-production` | 必须在真实部署中替换；建议只放受保护的环境文件 |
| `RUNTIME_AGENT_CONFIG_PATH` | 空 | 固定 TOML 路径；为空时只启用 Mock，显式路径无效时启动失败 |
| `RUNTIME_AGENT_DB_PATH` | 服务目录下 `var/runtime-agent.sqlite3` | SQLite WAL 任务日志 |
| `RUNTIME_AGENT_ARTIFACTS_DIR` | 服务目录下 `var/artifacts` | Agent 产物目录 |
| `RUNTIME_AGENT_ALLOW_UNAUTHENTICATED_LOOPBACK` | `true` | 是否允许回环免 token |
| `RUNTIME_AGENT_REQUIRE_AUTH_NON_LOOPBACK` | `true` | 非回环是否强制 token；必须保持 true |
| `RUNTIME_AGENT_GPU_CAPACITY` | `1` | `gpu` 资源组并发 |
| `RUNTIME_AGENT_CPU_MOTION_CAPACITY` | `2` | `cpu_motion` 资源组并发 |
| `RUNTIME_AGENT_POLL_INTERVAL_MS` | `20` | 调度轮询间隔 |

Agent 默认只注册确定性 Mock adapters。标准模板为 `runtime_agent/config.example.toml`；Windows 初始化脚本会把它复制为 `<RuntimeRoot>\config\runtime-agent.toml`，默认仍是 `mode="mock"`。Ollama、ComfyUI、LightX2V、FFmpegMotion 只有在 TOML 的 registry 模式和对应 adapter 开关都显式启用后才会替换同能力的 Mock。

建议显式落到固定根目录：

```powershell
$env:RUNTIME_AGENT_BEARER_TOKEN = '<长随机令牌>'
$env:RUNTIME_AGENT_DB_PATH = 'E:\AI\ai-story-runtime\data\runtime-agent.sqlite3'
$env:RUNTIME_AGENT_ARTIFACTS_DIR = 'E:\AI\ai-story-runtime\artifacts'
$env:RUNTIME_AGENT_CONFIG_PATH = 'E:\AI\ai-story-runtime\config\runtime-agent.toml'
$env:RUNTIME_AGENT_ALLOW_UNAUTHENTICATED_LOOPBACK = 'true'
$env:RUNTIME_AGENT_REQUIRE_AUTH_NON_LOOPBACK = 'true'
$env:RUNTIME_AGENT_GPU_CAPACITY = '1'
$env:RUNTIME_AGENT_CPU_MOTION_CAPACITY = '2'
$env:RUNTIME_AGENT_POLL_INTERVAL_MS = '20'
```

### 3.1 固定 TOML 与原子 reload

配置优先级是：核心进程参数可由环境变量覆盖 TOML 的 `[agent]`；adapter registry 始终从 `RUNTIME_AGENT_CONFIG_PATH` 指向的同一文件构建。旧 `runtime_agent/config/real-adapters.example.json` 不再是激活入口。

真实 adapter 的最小启用条件：

- `[registry].mode = "configured"`；缺省或 `mock` 时无论 adapter 开关为何值都只加载 Mock。
- 目标 `[adapters.<name>].enabled = true`。
- `model_ids` 是非空、无重复的精确白名单。
- `model_version` 已替换示例占位符，填写固定 digest、SHA-256 或人工核验的不可变版本。
- `workflow_version` 固定，并且调用请求必须完全匹配。
- Ollama/ComfyUI URL 只能指向 `127.0.0.1`、`localhost` 或 `::1`。
- ComfyUI manifest 的 `workflow_version`、`model_ids`、`model_version` 必须与 TOML 相同；图片编辑 workflow 必须显式绑定 `input_artifacts`。
- LightX2V 模型目录和受控 argv 的可执行程序必须存在；FFmpeg 可执行程序必须存在。命令始终以 `shell=False` 运行。

模板中的所有 adapter 都是 `enabled=false`，并且版本字段是不可激活的占位值。Agent 不会根据配置下载模型、安装节点、更新 workflow 或探测厂商最新版本。

配置修改后的安全切换：

```powershell
$headers = @{ Authorization = "Bearer $env:RUNTIME_AGENT_BEARER_TOKEN" }
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:9100/v1/runtime-reloads' `
  -Headers $headers -ContentType 'application/json' `
  -Body '{"reason":"activate-pinned-runtime-config"}'
```

reload 会先在临时对象中解析并完整校验所有启用项，成功后才替换 registry 并增加 generation。失败返回 `409 RUNTIME_CONFIG_INVALID`，不会写入成功 reload 记录，也不会替换原 registry；修正配置前继续使用旧 generation。

## 4. 推理领域配置

本次新增的 Django 控制面包括：

- `RuntimeNode`：Agent URL/版本、硬件快照、资源组、总/保留槽位、本地标记、健康和并发。
- `GenerationProfile`：模型、默认参数、允许覆盖参数和硬限制。
- `GenerationRoute` / `GenerationTarget`：全局/项目作用域、阶段/档位、目标角色/位置、超时、attempt 上限和冷却。
- `ProjectAISettings`：项目档位、`prefer_local=true`、付费/素材出站授权和 `project_budget_cny=0`。
- `ProviderPriceRate`：token/图片/视频秒/视频任务等计价、人民币汇率、来源、条件和版本。
- `AIBudgetPolicy` / `BudgetReservation`：默认硬限额 `0`、预留、结算、释放、过期、结果不明与人工复核。
- `GenerationWorkItem`：项目范围幂等、租约/心跳/版本、重试等待、路由快照与 Agent job 关联。
- `MediaArtifact`：产物状态、生命周期、SHA-256、过期、保护、隔离和软删除事实。

这些领域服务已实现；只有在迁移应用、数据配置和测试通过后，才算运行环境已验证。SQLite 只能验证 schema/单进程合同；并发预算预留与工作项领取必须在一次性 PostgreSQL 上验证，租约/流式恢复必须另用 Redis/Celery 环境验证。

## 5. Provider 与 Runtime Agent

ModelProvider 本次支持本地部署元数据和 Runtime Agent 执行器。真实模型启用前，至少配置并核验：

- `deployment_mode=local`。
- 对应 RuntimeNode 的 Agent URL 与 token/凭据引用。
- `runtime_model_id` 与 `runtime_adapter`。
- 能力与执行器一致。
- `is_active=false`，直到真实模型、工作流和硬件基准通过。

示例只表达字段，不代表模型存在：

```text
name: Local Runtime Text
provider_type: llm
deployment_mode: local
runtime_node: <127.0.0.1:9100 节点>
runtime_model_id: <实际安装后填写>
runtime_adapter: ollama
is_active: false
```

Ollama 直连兼容路径和 ComfyUI 原有 Provider 仍可作为回滚路径，但应在隔离环境分别验证。

## 6. 路由安全默认

代码默认：

- 新路由关闭：`AI_ROUTER_V2_ENABLED=false`。
- 影子模式开启：`AI_ROUTER_V2_SHADOW_MODE=true`。
- ProjectAISettings 优先本地：`prefer_local=true`。
- 付费回退关闭：`allow_paid_fallback=false`。
- 素材出站关闭：`allow_cloud_data_transfer=false`，授权人/时间为空。
- 项目付费预算：`project_budget_cny=0`。

路由实现会按规则、健康、冷却、优先级、权重和本地偏好排序；它本身不证明真实节点健康，也不能替代预算预留。

## 7. 默认预算为 0

项目启用新路由前，必须绑定一个明确的 AIBudgetPolicy：

```text
hard_limit: 0.000000
soft_limit: 0.000000
allow_overage: false
exhausted_action: block
is_active: true
```

`0` 表示禁止付费，不表示不限额。AIBudgetPolicy `hard_limit` 与 ProjectAISettings `project_budget_cny` 的代码默认都为 `0`；不要改成 `NULL`，因为预算服务会把它视为没有硬限额。金额按六位小数 Decimal 存储。

自动付费回退必须同时满足：项目 `allow_paid_fallback=true`、有效价目表、`project_budget_cny`、`daily_limit_cny`、`monthly_limit_cny` 均为正、预算预留成功和数据允许外发。用户显式 API 重生成不要求开启自动回退，但仍要求云授权、价格、上述预算、单次费用确认上限和幂等键。

## 8. 明文密钥风险

当前 ModelProvider `api_key` 和 RuntimeNode `access_token` 都是普通数据库字符字段；`credential_ref` 是更安全的目标引用字段，但不能消除兼容明文字段风险。因此：

- 数据库、备份、管理导出和调试转储按含密钥数据保护。
- 远程节点只保存凭据引用或受控 token，不把密钥放进工作流。
- 日志遮罩 `Authorization`、`api_key`、token、签名 URL 和错误详情。
- 泄露时禁用 Provider、轮换密钥并核查使用记录。

## 9. 回滚开关

当前真实开关：

```powershell
$env:AI_ROUTER_V2_ENABLED = 'false'
$env:AI_ROUTER_V2_SHADOW_MODE = 'true'
$env:AI_ARTIFACT_CLEANUP_ENABLED = 'false'
```

同时设置 `allow_paid_fallback=false`、`allow_cloud_data_transfer=false`、`project_budget_cny=0`，审计/撤销 `cloud_authorized_by/at`，把 Runtime Agent Provider 设为 `is_active=false`，恢复已验证 Provider。停止 Agent 前先排空或取消任务并对账预算；不要为回滚删除审计证据。

## 10. 配置检查

- [ ] 根目录为 `E:\AI\ai-story-runtime`，或有批准记录。
- [ ] `9100/11434/8188` 未暴露公网。
- [ ] 新路由默认关闭、影子模式开启。
- [ ] 项目付费/素材出站关闭，`project_budget_cny` 与硬预算明确为 `0`。
- [ ] token 一致但未出现在日志、文档或截图。
- [ ] Provider 默认禁用到真实基准通过。
- [ ] 工作流、模型、custom nodes 有版本与哈希。
- [ ] 自动清理仍关闭，直到备份恢复演练完成。
