# 模型与工作流指南

> Ollama、ComfyUI、LightX2V、FFmpegMotion adapter 已能从固定 TOML 显式注册，但示例配置为 `mode="mock"` 且全部 `enabled=false`。本文不声明任何模型/进程已安装、获得商用授权或通过本机基准。真实结果写入 [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md)。

## 1. 选择原则

模型选择按任务阶段而不是按品牌统一决定：质量、结构化输出成功率、显存、延迟、许可证、中文能力和维护成本缺一不可。候选模型只有在目标硬件、真实工作流和代表性数据上通过验证后，才能进入默认路由。

## 2. 候选映射

| AI Story 阶段 | 当前代码可接入面 | 本地候选方向 | 当前验证状态 |
| --- | --- | --- | --- |
| 改写、分镜、镜头描述 | Runtime Agent Ollama / 旧 OpenAI-compatible 路径 | Ollama 托管的 Qwen 系列等 | adapter 可配置；未安装、未基准 |
| 文生图 | Runtime Agent ComfyUI `text2image` | FLUX.2 Klein / Qwen Image 等兼容工作流 | adapter/manifest 合同已实现；未安装、未基准 |
| 图像编辑 | Runtime Agent ComfyUI `image_edit` | 支持参考图/局部编辑的 ComfyUI 工作流 | adapter/输入产物绑定已实现；未安装、未基准 |
| 图生视频 | Runtime Agent LightX2V | LightX2V（可承载经审核的 Wan 系列工作流） | adapter/受控 CLI 已实现；未安装、未基准 |
| 静态图运镜/拼接 | Runtime Agent FFmpegMotion | FFmpeg zoom/pan/crossfade/精确裁剪 | adapter 可配置；固定二进制未验证 |

模型名称会变化；落地时应记录准确仓库、版本、量化、许可证、文件哈希和下载日期。

## 3. 文本模型接入

Ollama 默认地址为 `http://127.0.0.1:11434`。检查服务和模型清单：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5
```

Runtime Agent Ollama adapter 调用 OpenAI-compatible `/v1/chat/completions`，并校验 `model_id` 与 `workflow_version`。旧 Django LLM 直连仍可回滚，配置时确认 base URL 的路径约定与所用兼容层一致。测试必须覆盖：

- 中文长文本改写。
- 严格 JSON 输出和字段完整性。
- 上下文过长与截断。
- 超时、取消和重试。
- 同一提示词在固定参数下的稳定性。

不要只用聊天问答判断是否适合分镜结构化任务。

## 4. ComfyUI 工作流接入

Runtime Agent ComfyUI adapter 从 TOML 指定的 manifest 加载固定 workflow，向 `/prompt` 提交、轮询 `/history/{id}`、再从 `/view` 获取产物。reload 会核对 manifest 的 `workflow_version`、`model_version`、`model_ids` 和必要 bindings；`image_edit` 必须显式绑定 `input_artifacts`。客户端不能上传任意 workflow JSON。

仓库旧 `core.ai_client.comfyui_client.ComfyUIClient` 仍把传入的 `prompt` 解析为完整 workflow JSON，仅用于兼容/回滚。普通自然语言不能直接传给旧客户端；新配置不要把旧客户端合同与 Runtime Agent manifest 合同混用。

每个生产工作流应登记：

- `workflow_id` 与语义版本。
- 源 JSON 文件路径和 SHA-256。
- ComfyUI 版本、custom nodes 与 commit。
- 模型文件名、版本、量化和哈希。
- 可覆盖参数的 JSON Pointer 或节点 ID。
- 输入输出契约、最小显存、许可证。
- 已验证硬件和基准报告链接。

示例哈希：

```powershell
Get-FileHash 'E:\AI\ai-story-runtime\workflows\story-image-v1.json' -Algorithm SHA256
```

## 5. 参数注入规则

不要通过字符串替换修改工作流 JSON。当前 Runtime Agent 生产合同要求：

1. 加载已登记且哈希匹配的模板。
2. 按允许列表更新节点输入。
3. 校验尺寸、帧数、步数、seed 和文件路径。
4. 拒绝模板外节点、未知 custom node 和越界资源参数。
5. 保存模板版本和最终参数摘要，避免保存敏感完整提示词。

图像输入必须复制到受控输入目录或通过安全上传接口传递；禁止接受任意本机路径。

### 取消与超时

- LightX2V 和 FFmpeg 使用受控 argv、`shell=False` 与进程树监督；取消或超时会先温和终止、再强制清理子进程。仍需在真实 Windows GPU 环境验证 CUDA 显存是否及时释放。
- Ollama 是单次 HTTP 请求，只受请求 timeout 限制；Agent 已接受取消不等于 HTTP 请求立刻终止。
- ComfyUI 轮询会检查取消，但当前不主动调用 queue delete/interrupt。取消后必须检查 ComfyUI 队列、history 和显存，再决定是否重试。

## 6. 工作流晋级

工作流依次经过：

```text
草案 -> 本机开发验证 -> 代表性数据基准 -> 安全/许可证审核 -> 试运行 -> 默认路由
```

每一步都保存证据。以下任一情况不得晋级：

- 模型或 custom node 来源不明。
- 许可证与计划用途不兼容。
- 结构化输出成功率不足。
- 显存溢出后依赖无限重试。
- 工作流包含任意命令执行、远程下载或未审核节点。
- 结果只来自单个成功样例。

## 7. 版本与回滚

新版本不覆盖旧版本文件。路由记录明确版本，运行中任务固定使用创建时版本。回滚时：

1. 停止新任务进入问题版本。
2. 将默认版本指回上一个已验证版本。
3. 对运行中任务选择完成、取消或显式重试，不静默换工作流。
4. 保留失败样例、日志和哈希。

目标开关：

```powershell
$env:AI_ROUTER_V2_ENABLED='false'
$env:AI_ROUTER_V2_SHADOW_MODE='true'
```

关闭目标路由后，现有 Django Provider 直连仍需单独验证。

## 8. 质量抽检

文本检查 JSON 合法率、字段准确率、人物一致性和人工可用率；图像检查提示词遵循、角色一致性、文字伪影和安全性；视频检查帧间一致性、运动合理性、时长、编码和音视频边界。报告平均值、P95 和失败样例，不只报告最好结果。

### 视频分段合同

目标镜头总长为 8–10 秒，默认单次原生生成上限 5 秒、24 fps、相邻段重叠 8 帧。后续段接收上一段最后一帧，拼接后 crossfade，并按 `trim_exact_duration` 精确裁到目标总长。9 秒样例应规划为 5 秒首段与约 4.333 秒后段，而不是两个互不关联的片段。该规划服务已实现；真实 LightX2V/Wan 工作流的连贯性、显存和时延仍未基准。

## 9. 最小交付清单

- [ ] 模型来源、版本、哈希和许可证。
- [ ] 工作流 JSON、版本、custom node 锁定清单。
- [ ] 目标硬件基准与失败样例。
- [ ] 输入输出契约和资源上限。
- [ ] 取消、超时、OOM、节点离线演练。
- [ ] 旧版本回滚验证。
