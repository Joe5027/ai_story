# ADR-0004：统一推理工作项状态机

- 状态：已接受；Django 与 Agent 状态机已实现，完整任务接线验证待完成
- 日期：2026-07-13

## 背景

ProjectStage 表示业务阶段，Celery 表示消息执行，Django GenerationWorkItem 与 Runtime Agent job 表示一次推理执行。混用这些状态会导致重复执行、假取消、恢复困难和预算不一致。

## 决策

保留两个边界清晰的状态机。

### Django GenerationWorkItem

```text
waiting -> leased -> running -> succeeded
   ^          |         |------> failed
   |          |         |------> cancelled
   |          |         \------> retry_wait --到期/有限次数--> waiting
   |          \----------------> waiting/failed/cancelled
   \--------------------------------------------------------
```

| 状态 | 含义 |
| --- | --- |
| `waiting` | 等待 worker 认领 |
| `leased` | 已由指定 owner 在有效期内认领 |
| `running` | 执行中，attempt_count 增加 |
| `retry_wait` | 有限退避，等待 `next_retry_at` |
| `succeeded` | 成功终态 |
| `failed` | 失败终态 |
| `cancelled` | 取消终态 |

`retry_wait` 只能在未超过 `max_attempts` 且到达重试时间后回到 `waiting`；管理员显式 force 是可审计例外。`failed` 不自动返回队列。

### Runtime Agent job

```text
queued -> running -> succeeded | failed | cancelled
```

Agent 使用 SQLite WAL。重启时，未请求取消的 `running` Mock job 重新入队，已请求取消的 job 变为 `cancelled`。真实外部 adapter 仍需独立验证上游幂等。

## 固定执行事实

GenerationWorkItem 记录：

- 项目、能力、业务阶段、storyboard/tile/segment 索引。
- profile、route、target、Provider 与 RuntimeNode。
- 项目范围的字符串幂等键；同项目同 key 唯一。
- 请求/生效参数、路由快照、成本、用量和错误。
- Provider request ID 与 Agent job ID。
- attempt/max_attempts、version、lease owner/expiry、heartbeat 和 next retry。

重试不得无审计地换 Provider/模型；route_snapshot 与有效参数需经过敏感字段遮罩。

## 幂等、版本与租约

- 创建按 `project + idempotency_key` 幂等；同 key 不同能力或请求内容报冲突。
- 状态转换在事务中锁行，并可用 `expected_version` 拒绝旧 worker 写入。
- claim 必须给 lease_owner 和正数 lease；start 只允许有效 leased 项。
- heartbeat 只允许当前 owner，不能复活过期租约；每次变更递增 version。
- release lease 回 waiting；retry_wait 在时限和 attempts 条件满足后回 waiting。
- Agent 另用 1–200 字符 Idempotency-Key；同 key 同请求重放，同 key 不同请求返回 409。

## 取消与失败

- Django 非终态可转 cancelled，但业务层仍需确认上游副作用。
- Agent queued job 立即取消，running job 协作式取消。
- “接受取消”不表示真实外部生成已停止。
- 需要重试的错误进入 retry_wait；不可重试错误进入 failed。
- 已产生费用时结算实际金额并释放剩余预留；结果不明则预算转 ambiguous/manual_review。

## 恢复

1. lease 过期后旧 owner 不得继续写；恢复者依据 version 和上游 ID 对账。
2. 有 provider_request_id/agent_job_id 时先查询上游。
3. 没有证据证明请求未发送，不自动重放。
4. 预算与工作项按 ID 和 attempt 对账。
5. Agent SQLite 恢复仅覆盖本地 Mock job，不能替代 Django 恢复。

## 与业务状态关系

一个 ProjectStage 可对应多个 attempts，但只能采纳一个成功结果。业务阶段在结果校验、保存和选择完成后才推进。重置 ProjectStage 不得删除工作项、Agent journal 或预算审计。

## 被否决方案

### 只使用 Celery 状态

Celery 不知道外部请求、工作流版本、预算、租约和业务采纳状态。

### 用 Agent job ID 或 ComfyUI prompt ID 作为唯一业务状态

它们不能表达路由前、项目范围幂等和预算事实。

### 失败后直接重新投递

绕过 retry_wait、attempt 上限和版本/租约会造成重复生成与扣费。

## 影响

优点：业务状态、Django 编排和 Agent 各自职责清晰；具备项目幂等、租约、心跳、版本和有限重试基础。

代价：跨层必须关联 work_item_id/job_id；真实上游的不确定性仍需补偿和对账。

## 回滚

```powershell
$env:AI_ROUTER_V2_ENABLED='false'
$env:AI_ROUTER_V2_SHADOW_MODE='true'
```

停止创建新 GenerationWorkItem，停用 Agent Provider，排空/对账 waiting、leased、running 和 retry_wait 项后切回旧 ProjectStage/Celery 路径。不能删除工作项表或 Agent SQLite 来回滚。

## 验证要求

- 每个允许/禁止转换与终态测试。
- 项目范围幂等冲突、max attempts、错误遮罩测试。
- 并发 claim、旧 version、错误 owner、心跳和过期 lease 测试。
- Agent 重放/冲突、取消、容量和 SQLite 重启测试。
- Django client 与 Agent 顶层 schema 联调。
- 上游接受前后崩溃的真实 adapter 演练。
