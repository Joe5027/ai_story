# 远程推理节点指南

> 适用范围：将 GPU 推理放到另一台受控主机。默认本地端口仍为 Runtime Agent `9100`、Ollama `11434`、ComfyUI `8188`。远程部署尚未由本文证明可用，必须在目标网络和硬件上单独验证。

## 1. 推荐拓扑

```text
AI Story Django/Celery
        |
        | 仅访问受控入口（私网 + TLS + 身份认证）
        v
Runtime Agent :9100
        |-------------------|
        v                   v
Ollama :11434         ComfyUI :8188
```

Runtime Agent 控制面与真实 adapter registry 已实现；目标节点只有在固定 TOML 使用 `mode="configured"`、对应 adapter 启用且校验通过后才会代理本机推理服务。远程节点仍需逐机安装、激活和基准，不得临时把 Ollama/ComfyUI/LightX2V 暴露到公网。

## 2. 安全基线

- `11434` 和 `8188` 不对互联网开放。
- 优先使用同一内网、WireGuard/Tailscale 类私网或企业 VPN；跨网络必须 TLS。
- Runtime Agent 要求服务身份认证、短期凭据、速率限制和请求大小限制。
- 防火墙仅允许 AI Story 应用节点访问必要端口。
- 远程服务使用独立低权限账户；不能用管理员账户日常运行。
- 输入文件使用受控上传，不允许客户端指定远程任意路径。
- 健康接口不得返回密钥、完整提示词、用户素材或文件系统细节。
- Provider 密钥当前存在数据库明文风险，不能复制到远程节点日志或工作流 JSON。

## 3. 节点目录

Windows 推荐保持统一根目录：

```text
E:\AI\ai-story-runtime\
  artifacts\
  backups\
  cache\
  components\
  config\
  data\
  logs\
  models\
  run\
  staging\
  workflows\
```

远程 Linux 可使用等价受控目录，但必须在节点登记中保存真实绝对路径；不要让业务代码猜测盘符或 home 目录。

## 4. Windows 防火墙示例

以下是审阅模板，不要直接执行占位地址。执行防火墙写操作前需管理员批准：

```powershell
$AppNode = '<AI_STORY_PRIVATE_IP>'
New-NetFirewallRule -DisplayName 'AI Story Runtime Agent' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9100 `
  -RemoteAddress $AppNode
```

不要为 `11434` 或 `8188` 创建 `Any` 来源规则。若 Agent 与推理服务同机，推理端口只绑定 `127.0.0.1`。

## 5. 节点登记建议

目标节点记录至少包含：

| 字段 | 说明 |
| --- | --- |
| `node_id` | 稳定、不可复用的节点标识 |
| `agent_url` | TLS 或私网地址 |
| `capabilities` | `llm`、`text2image`、`image_edit`、`image2video`、`motion_render` |
| `models` | 精确版本、量化、哈希 |
| `workflows` | 允许的工作流 ID 与版本 |
| `capacity` | 显存、并发、磁盘下限 |
| `agent_version` / `hardware_snapshot` | Agent 与硬件证据 |
| `resource_groups` | `gpu`、`cpu_motion` 等容量 |
| `slot_count` / `reserved_slot_count` | 总槽位与保留槽位 |
| `data_classes` | 允许处理的数据等级 |
| `drain` | 是否停止接收新任务 |
| `last_probe_at` | 最近成功探测时间 |

静态登记不能代替实时健康检查。

## 6. 连通与健康检查

在应用节点执行：

```powershell
$AgentHost = '<RUNTIME_AGENT_PRIVATE_HOST>'
Test-NetConnection $AgentHost -Port 9100
$Headers = @{ Authorization = 'Bearer <REMOTE_AGENT_TOKEN>' }
Invoke-RestMethod "https://$AgentHost`:9100/v1/health/live" -TimeoutSec 5
Invoke-RestMethod "https://$AgentHost`:9100/v1/health/ready" -Headers $Headers -TimeoutSec 5
```

若暂时直连推理服务，仅在隔离网络测试：

```powershell
Test-NetConnection '<OLLAMA_PRIVATE_HOST>' -Port 11434
Test-NetConnection '<COMFY_PRIVATE_HOST>' -Port 8188
```

探测成功只说明端口/接口可达，不说明模型已安装、工作流可运行或性能达标。

## 7. 上线流程

1. 核验硬件、驱动、磁盘、时间同步和服务账户。
2. 安装模型与工作流，记录来源、许可证、版本和哈希。
3. 只在私网启动服务，应用防火墙和认证。
4. 运行健康、功能、安全和失败演练。
5. 将节点设为 drain，跑独立基准。
6. 小流量启用，观察队列、显存、错误率和结果质量。
7. 通过审批后解除 drain。

## 8. 节点排空与维护

维护前设置 drain，停止新任务进入，等待运行中工作项完成。长视频等任务若不能及时完成，按显式取消协议处理。LightX2V/FFmpeg CLI 会尝试清理进程树；Ollama/ComfyUI HTTP 调用仍可能持续到请求 timeout，ComfyUI 提交任务也可能继续留在其队列。不能只看 Agent `202` 或杀掉 Agent 就假装上游已取消；恢复后要核对上游任务、工作项状态、显存、产物和预算。

## 9. 故障与演练

### 网络分区

- 期望：请求有限超时，工作项进入可恢复状态；预算为 `0` 时不走付费回退。
- 恢复：先确认远程任务是否实际已接受，再决定重试，避免重复生成。

### 节点磁盘不足

- 期望：提交前预检失败，节点进入 drain，已有产物不被自动删除。
- 恢复：按保留策略人工审批清理或扩容，随后跑一个已知样例。

### 节点凭据泄露

- 立即撤销凭据、隔离节点、检查访问日志和任务范围。
- 轮换相关 Provider 密钥；因当前明文存储风险，还要检查数据库和备份访问。

### GPU/驱动故障

- 停止新任务，保留驱动与模型日志，迁移到已验证节点。
- 不自动切换到未知模型或未授权的付费 Provider。

## 10. 回滚

```powershell
$env:AI_ROUTER_V2_ENABLED='false'
$env:AI_ROUTER_V2_SHADOW_MODE='true'
```

将节点设为 drain，把项目付费/素材出站关闭、项目预算与硬预算设为 `0`，停用远程 Agent Provider 并恢复已验证 Provider。确认旧 worker 和计划任务不再访问远程节点。
