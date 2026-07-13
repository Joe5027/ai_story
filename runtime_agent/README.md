# AI Story Runtime Agent

这是一个与现有 Django/Vue 应用解耦的本地 FastAPI v1 运行时服务。未指定固定 TOML 或 TOML 保持 `mode="mock"` 时只加载确定性的 Mock adapters；服务本身不安装、下载或升级任何模型。

## 已实现契约

- `GET /v1/health/live`：进程存活，固定免鉴权。
- `GET /v1/health/ready`：SQLite、产物目录、调度线程就绪检查。
- `GET /v1/capabilities`：能力、资源组与并发容量。
- `POST /v1/jobs`：必须携带 `Idempotency-Key`，首次返回 `202`，同键同请求返回原任务和 `200`，同键不同请求返回 `409`。
- `GET /v1/jobs`、`GET /v1/jobs/{job_id}`：任务列表和查询。
- `DELETE /v1/jobs/{job_id}`：固定返回 `202`；queued 任务立即取消，running 任务协作式取消。
- `GET /v1/jobs/{job_id}/artifacts`、`GET /v1/artifacts/{id}/metadata`：产物元数据。
- `GET /v1/artifacts/{id}`：Django client 使用的鉴权下载，并在返回前复核文件大小和 SHA256。
- `POST/GET /v1/runtime-reloads`：从同一个固定 TOML 原子重建 adapter registry；配置失败时保留原 registry 和 generation。

任务状态固定为 `queued/running/succeeded/failed/cancelled`。SQLite 使用 WAL journal；进程重启时，未请求取消的 `running` 任务会重新入队，已请求取消的任务会落为 `cancelled`。

资源组容量固定由配置控制，默认 `gpu=1`、`cpu_motion=2`。`llm`、`text2image`、`image_edit`、`image2video` 使用共享且独占的 `gpu` 槽，只有 `motion_render` 使用 `cpu_motion`。

## 真实适配骨架边界

`runtime_agent/real_adapters.py` 提供不依赖模型安装即可做配置测试的四条真实路径：

- `OllamaAdapter`：固定调用 OpenAI-compatible `/v1/chat/completions`。
- `ComfyUIAdapter`：读取 `manifest_version=1` 的版本化 workflow，显式绑定 prompt、negative prompt、width、height、seed 与输入产物，并实现 `/prompt`、`/history/{id}`、`/view` 协议。
- `LightX2VAdapter`：仅接受受控 argv 模板，使用 `subprocess.run(..., shell=False)`。
- `FFmpegMotionAdapter`：生成 zoom、pan、crossfade 计划；每段不超过 5 秒，相邻段固定 8 帧 overlap，并以 `shell=False` 执行 FFmpeg。

标准配置见 `config.example.toml`，ComfyUI manifest 结构示例见 `config/workflows/comfyui-v1.example.json`。旧 `config/real-adapters.example.json` 只保留为早期骨架参考，运行时不读取它。

真实适配器必须同时满足两道显式开关：

1. `[registry].mode = "configured"`；
2. 对应 `[adapters.*].enabled = true`。

启用项还必须提供非空模型白名单、已替换占位符的固定模型版本/摘要和固定 `workflow_version`。ComfyUI 在启动或 reload 时就校验 manifest 版本、模型白名单、模型版本和输入绑定；LightX2V/FFmpeg 校验本地路径或可执行程序。请求中的 `model_id` 或 `workflow_version` 不匹配时返回 `RUNTIME_NOT_READY`，绝不会静默改用另一个模型或 Mock。所有 HTTP 子运行时地址只允许本机回环地址。

所有错误使用稳定 envelope：

```json
{
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "Idempotency-Key 已用于不同的请求体",
    "retryable": false,
    "details": {}
  },
  "request_id": "..."
}
```

## 本地运行

```powershell
cd runtime_agent
Copy-Item .env.example .env
$env:RUNTIME_AGENT_BEARER_TOKEN = "replace-with-a-long-random-token"
uv sync --extra test
uv run python -m runtime_agent
```

不设置 `RUNTIME_AGENT_CONFIG_PATH` 时固定使用 Mock。`config.example.toml` 面向复制到 `<RuntimeRoot>\config\runtime-agent.toml` 的部署方式，其相对路径也以该 TOML 所在目录解析。不要直接把示例中的 `model_version = "replace-with-..."` 当作真实版本；完成模型/工作流安装和基准后，逐项填写固定摘要，再人工切换 mode 和 enabled。修改后调用 `POST /v1/runtime-reloads`；若任何启用项校验失败，HTTP 返回 `409 RUNTIME_CONFIG_INVALID`，正在使用的 registry 不会被替换。

默认监听 `127.0.0.1:9100`。示例创建任务：

```powershell
$headers = @{
  Authorization = "Bearer $env:RUNTIME_AGENT_BEARER_TOKEN"
  "Idempotency-Key" = "demo-001"
}
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:9100/v1/jobs `
  -Headers $headers -ContentType application/json `
  -Body '{"capability":"motion_render","model_id":"mock/default","profile":"draft","prompt":"镜头轻微推进","negative_prompt":null,"input_artifacts":[],"output_spec":{"mock":{"delay_ms":25}},"seed":7,"workflow_version":"v1","project_id":"project-1","stage_type":"camera_movement","work_item_id":"shot-1"}'
```

## 鉴权边界

`/v1/health/live` 始终公开。其他端点接受不透明 Bearer token。默认允许回环地址免 token，同时强制非回环地址提供 token；两项策略均可通过 `.env.example` 中的布尔配置独立测试。对外监听时应设置长随机 token，并保持 `RUNTIME_AGENT_REQUIRE_AUTH_NON_LOOPBACK=true`。

## 独立依赖与锁策略

本目录的 `pyproject.toml` 是唯一依赖声明，不继承仓库根依赖。锁文件策略如下：

1. 在允许访问包索引的受控依赖更新窗口执行 `uv lock`，提交本目录生成的 `uv.lock`。
2. CI/生产使用 `uv sync --frozen --no-dev`；测试环境使用 `uv sync --frozen --extra test`。
3. 离线开发只使用已提交锁文件和已有 uv cache，执行 `uv sync --offline --frozen --extra test`，不得在运行任务时临时解析新版本。
4. 本次实现不下载模型；Mock adapters 仅使用本地 CPU、SQLite 和文件系统。

如果当前机器没有缓存 FastAPI 测试依赖，离线模式无法首次创建虚拟环境；这属于依赖预热限制，不应通过修改根项目依赖规避。

## 验证

```powershell
cd runtime_agent
uv run --extra test pytest
```

契约测试覆盖鉴权、非回环策略、幂等冲突、五类适配器、五种状态、取消、SQLite 重启恢复、资源容量、reload、稳定错误、产物 SHA256 与鉴权下载。

不依赖 FastAPI 的真实适配配置/绑定测试可以单独运行：

```powershell
python -m pytest -q offline_tests
python scripts/core_smoke.py
```
