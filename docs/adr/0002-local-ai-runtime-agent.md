# ADR-0002：引入本地 AI Runtime Agent 控制面

- 状态：已接受；Mock v1 与真实 adapter 骨架已实现，真实路径未激活
- 日期：2026-07-13
- 默认地址：`http://127.0.0.1:9100`
- 默认运行目录：`E:\AI\ai-story-runtime`

## 背景

现有 Django/Celery 代码直接调用 Provider。随着 Ollama、ComfyUI、远程 GPU 节点、工作流版本、容量、取消和恢复需求增加，把操作系统进程、节点探测和推理工作项全部塞入业务模型会扩大耦合和故障面。

仓库 `runtime_agent/` 已实现 FastAPI Mock 纵切，以及 Ollama、ComfyUI、LightX2V、FFmpegMotion 真实 adapter 骨架。默认 registry 仍只激活 Mock；模型和进程未安装，不能作为真实推理证据。

## 决策

引入轻量 Runtime Agent 作为本地/远程推理控制面。它负责：

- 暴露 `/v1/health/live|ready`、能力、幂等 job、取消、产物和 runtime reload 契约。
- 使用 SQLite WAL 保存 Mock job，并在进程重启时恢复未完成任务。
- 用 `gpu` 和 `cpu_motion` 资源组限制并发。
- 为 Django Runtime Agent client 提供统一顶层 v1 schema。
- 输出脱敏状态、指标和审计事件。

它不负责：

- 替代 Django 的项目、用户、故事和业务阶段管理。
- 充当任意命令执行器或文件浏览器。
- 在没有登记的情况下下载模型/custom nodes。
- 保存或返回 Provider 明文密钥。
- 直接实现真实模型推理；当前结果由 Mock adapters 生成，未来推理由 Ollama、ComfyUI、LightX2V 或外部 Provider 完成。
- 决定付费回退或操作项目预算；这些留在 Django。

## 进程与端口

| 服务 | 默认绑定 | 作用 |
| --- | --- | --- |
| Runtime Agent | `127.0.0.1:9100` | 控制面 |
| Ollama | `127.0.0.1:11434` | 文本推理 |
| ComfyUI | `127.0.0.1:8188` | 图像/视频工作流 |

远程 Agent 必须通过私网和 TLS，推理端口不直接暴露公网。

## 文件边界

推荐部署目录：

```text
E:\AI\ai-story-runtime\
  config\       # 非密钥配置或密钥引用
  logs\         # 脱敏、轮转
  workflows\    # 版本化 JSON 和清单
  models\       # 模型或模型清单
  inputs\       # 受控输入
  outputs\      # 受控结果
  tmp\          # 可重建临时数据
```

真实路径激活前必须补齐统一的输入产物引用与路径边界；当前不能把 Agent 暴露给不受信任调用者或允许任意路径输入。

## 已实现可靠性边界

- 写操作要求幂等键。
- Mock job 持久化，进程重启后将未取消的 running 任务重新入队。
- 资源组调度限制并发。
- runtime reload 原子替换 Mock registry 并记录 generation。
- 健康接口轻量，不加载模型或触发生成。

真实 adapter 的上游幂等、超时不确定性和分布式 lease 尚需在接入时补充。Django 状态机详见 [0004-work-item-state-machine.md](0004-work-item-state-machine.md)。

## 安全

- 只绑定必要接口，远程访问必须认证授权。
- 已有资源组并发与 ComfyUI manifest 显式绑定；统一请求大小、文件类型和所有输入路径允许列表尚未完整落地，真实激活前必须补齐。
- 日志不记录授权头、密钥、完整提示词或用户原始素材。
- 当前 Django Provider key 与 RuntimeNode access_token 都是普通字符字段；Agent 不能扩大该明文风险，后续应迁到短期凭据或密钥引用。
- custom nodes 等同代码依赖，必须锁版本和审核来源。

## 被否决方案

### 所有逻辑继续放 Django/Celery

初期简单，但操作系统/硬件控制与业务层耦合，远程节点和恢复逻辑难以隔离。

### 让 ComfyUI/Ollama 直接公开给前端

被否决。缺少业务授权、数据分类、工作流允许列表、预算和统一审计。

### Agent 同时管理模型下载和系统安装

默认不采用。安装是高权限供应链操作，应由明确的运维流程完成。

## 影响

优点：隔离硬件/工作流复杂度，提供统一状态与远程节点入口，便于故障演练。

代价：新增服务、部署、认证、持久化和监控；若边界不清可能与 Celery 状态重复。

## 回滚

```powershell
$env:AI_ROUTER_V2_ENABLED='false'
$env:AI_ROUTER_V2_SHADOW_MODE='true'
```

停用 Runtime Agent Provider，排空或显式取消现有项，然后恢复 Django 已验证的 Provider 直连路径。不能仅停 `9100` 而忽略仍在上游运行的任务。

## 验证要求

- Mock Agent 重启、幂等、取消、容量和 SQLite 恢复契约测试。
- 真实 adapter 激活后再做网络分区、Ollama/ComfyUI/LightX2V 中断、上游超时和磁盘不足演练。
- 任意路径、未知工作流、超限参数和未认证请求被拒。
- 日志和响应密钥扫描。
- 已实现的 Mock API 契约见 [../local-ai/API_REFERENCE.md](../local-ai/API_REFERENCE.md)。
