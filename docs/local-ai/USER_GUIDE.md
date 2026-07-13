# 本地 AI 用户指南

> 状态：Runtime Agent 默认配置只有 Mock；管理员可用固定 TOML 显式激活真实 adapter，但必须先安装并基准对应模型/工作流。`GET /v1/capabilities` 出现真实 adapter 也只证明配置已加载，不能替代质量和硬件验收。详细状态见 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)。

## 1. 适用角色

- 创作者：选择项目模型、发起阶段生成、查看失败原因；
- 管理员：安装运行时、配置 Provider、控制预算和回退；
- 运维人员：监控队列、执行故障演练和恢复；
- 开发者：验证工作流、API 合同和状态机。

## 2. 开始前确认

管理员应提供以下证据，而不是口头确认：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing
Invoke-RestMethod http://127.0.0.1:9100/v1/health/live
$Headers = @{ Authorization = 'Bearer <RUNTIME_AGENT_TOKEN>' }
Invoke-RestMethod http://127.0.0.1:9100/v1/health/ready -Headers $Headers
```

预期边界：

- 11434 与 8188 可以在各自软件安装后独立可用；
- 9100 的 live/ready 只证明 Agent 控制链与本地状态目录就绪；再查 `/v1/capabilities` 才能知道当前能力是 Mock 还是真实 adapter；
- HTTP 200 只证明服务存活，不证明目标模型存在或生成质量达标；
- 不要把上述输出与环境变量全量截图发送到公共渠道。

## 3. 项目内的推荐阶段路线

| 阶段 | 默认本地路线 | 失败时行为 |
| --- | --- | --- |
| `rewrite` | Ollama 文本模型 | 保留失败；默认不付费回退 |
| `asset_extraction` | 同一文本模型，强制 JSON Schema | JSON 不合法时有限重试，不静默换供应商 |
| `storyboard` | 文本模型，结构化分镜输出 | 校验失败后进入有限重试或 `failed` |
| `camera_movement` | 文本模型或规则模板 | 可降级为静态运镜模板 |
| `image_generation` | ComfyUI 图像工作流 | 保留工作项和诊断信息 |
| `multi_grid_image` | ComfyUI 生成整图，现有 PIL 服务切片 | 切片失败不重新计费生成原图 |
| `image_edit` | Runtime Agent/ComfyUI 编辑工作流 | adapter 可配置；默认关闭，真实 workflow 未安装/未基准 |
| `video_generation` | LightX2V；草稿可用 FFmpegMotion | adapter 可配置；默认关闭，真实模型/进程未基准 |

模型与工作流仍需按 [MODEL_WORKFLOW_GUIDE.md](MODEL_WORKFLOW_GUIDE.md) 安装并通过基准。

## 4. 配置本地 Provider

新本地配置首选 Runtime Agent，不直接让 Django 管理 Ollama/ComfyUI 进程：

1. 在“本地 AI 运行节点”创建 RuntimeNode，URL 为 `http://127.0.0.1:9100`，写入与 Agent 一致的 token。
2. 管理员执行节点“刷新健康”，确认 `capabilities` 中是预期 adapter、模型白名单和 workflow 版本。
3. 在模型管理创建 `deployment_mode=local` 的 Provider，绑定 RuntimeNode，填写精确 `runtime_model_id` 与 `runtime_adapter`。
4. 基准未通过前保持 `is_active=false`；通过后先加入单项目路由，不直接切全局默认。

示例字段：

| 字段 | 文本示例 | 图片示例 |
| --- | --- | --- |
| `provider_type` | `llm` | `text2image` 或 `image_edit` |
| `deployment_mode` | `local` | `local` |
| `runtime_node` | 本机 9100 节点 | 本机 9100 节点 |
| `runtime_adapter` | `ollama` | `comfyui` |
| `runtime_model_id` | TOML 白名单中的精确 ID | TOML/manifest 白名单中的精确 ID |
| `api_key` | 可留空 | 可留空 |

Ollama OpenAI-compatible 直连与旧 `ComfyUIClient` 仍是兼容/回滚路径，不是新本地配置首选。旧 `ComfyUIClient` 把 `prompt` 当完整 workflow JSON；Runtime Agent ComfyUI adapter 则加载固定 manifest 并只注入允许字段，不能混用两种请求契约。

## 5. 发起生成

1. 在“项目 AI 设置”选择质量档，保持“优先本地”开启；只绑定已经过基准的本地 Provider。
2. 先执行单个阶段、单个分镜，不直接运行全集。
3. 记录工作项 ID、Provider、模型、workflow 版本和 seed。
4. 等待终态：`succeeded`、`failed` 或 `cancelled`。
5. 检查实际产物，而不是只看进度到 100%。
6. 扩大到批量前检查显存、磁盘剩余量和队列等待时间。

Runtime Agent 使用 `queued/running/succeeded/failed/cancelled`；Django GenerationWorkItem 使用 `waiting/leased/running/retry_wait/succeeded/failed/cancelled`。ProjectStage 仍表示业务阶段，不要混为同一状态。

## 6. 预算为 0 时会发生什么

默认预算 `0` 的含义是：

- 本地推理可以继续，因为其 Provider 估算的外部费用为 0；
- 任何 `billing_mode=paid` 的回退必须被拒绝；
- 拒绝应显示为预算原因，不应伪装成模型故障；
- 管理员必须显式提高预算并开启付费回退，才允许外部调用；
- 已经发出的云端任务可能无法撤销，预算检查必须发生在提交之前。

## 7. 识别降级与回退

“本地 AI”控制页、项目工作项和调用账本应至少显示：

- 实际 Provider 和模型；
- 是否发生本地重试；
- 是否换过候选目标及其错误分类；
- 付费预算的预留、结算或释放结果；
- 最终产物是否来自本地或外部 Provider。

若某个旧页面没有展示这些字段，应转到工作项/调用账本核对；不能在信息缺失时确认付费。

## 8. 用户主动使用 API 重生成

审美、角色一致性或个人偏好不满意不会自动触发付费。推荐流程：

1. 在项目 AI 设置中确认本项目确实允许数据出站；第一次开启必须再次确认。
2. 管理员维护该 Provider/模型/能力的有效价目表，并设置项目、全局每日和全局每月预算。
3. 对目标 Provider 运行执行前估算，确认 `missing_configuration` 为空。
4. 在工作项页选择一个 `succeeded/failed` 终态工作项，点击“使用 API 重生成”。
5. 核对最大预计费用后确认；系统提交精确 Provider、费用上限和幂等键。
6. 在调用账本核对预留、结算、Provider、价格版本与新工作项结果。

按整个阶段重生成时也必须先做精确 Provider 估算，并通过阶段确认入口。该功能要求 `AI_ROUTER_V2_ENABLED=true`；页面在网络超时或 5xx 后会复用同一幂等键，用户不要另开一次操作重复提交。

完整流水线若包含直接 API Provider，会返回 `PAID_PIPELINE_CONFIRMATION_REQUIRED` 并停止排队。当前没有“确认整条流水线”的按钮或后端参数；请逐阶段估算和执行。

## 9. 安全使用

- 提示词中避免包含不必要的身份证、联系方式、合同和未公开素材；
- 上传参考图前确认有权处理并生成衍生内容；
- 不把模型列表命令和密钥读取命令放在同一段脚本中；
- 不在问题工单中粘贴完整数据库记录；
- 对人物、品牌、医疗、政治和儿童内容执行更严格人工审查；
- 本地模型没有云供应商安全兜底，失败或违规输出由部署方负责。

## 10. 紧急停止

用户侧先停止继续提交。管理员随后执行：

```powershell
$env:AI_ROUTER_V2_ENABLED = 'false'
$env:AI_ROUTER_V2_SHADOW_MODE = 'true'
```

同时将项目 `allow_paid_fallback=false`、`allow_cloud_data_transfer=false`、`project_budget_cny=0`，硬预算设回 `0`，停用 Runtime Agent Provider 并排空相关队列。不要直接删除 `E:\AI\ai-story-runtime`。

## 11. 获取帮助时提供

- 发生时间与时区；
- 项目 ID、阶段、分镜 ID 和工作项 ID；
- Provider/模型/workflow 版本；
- HTTP 状态、错误码与已脱敏日志；
- `nvidia-smi` 摘要、磁盘剩余量和端口状态；
- 是否启用了回退、预算值和最后一次成功时间。

不要提供 API Key、Authorization 头、完整私有提示词或带签名的产物 URL。
