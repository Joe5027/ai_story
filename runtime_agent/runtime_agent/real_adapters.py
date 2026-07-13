"""真实本地运行时适配骨架；缺少进程时明确返回 RUNTIME_NOT_READY。"""

from __future__ import annotations

import copy
import json
import os
import shutil
import string
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .adapters import ExecutionContext
from .errors import AdapterRuntimeError, RuntimeNotReady
from .process_supervisor import run_cancellable_process


class HttpTransport(Protocol):
    def json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]: ...

    def bytes(self, url: str, *, timeout: float) -> tuple[bytes, str]: ...


class UrllibTransport:
    """只用标准库访问本机 HTTP runtime，便于独立部署。"""

    def json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AdapterRuntimeError(
                "RUNTIME_REQUEST_FAILED",
                f"本地 runtime 返回 HTTP {exc.code}",
                retryable=exc.code >= 500,
                details={"status_code": exc.code},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeNotReady("无法连接本地 runtime", {"reason": type(exc).__name__}) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterRuntimeError(
                "OUTPUT_SCHEMA_INVALID",
                "本地 runtime 返回了无效 JSON",
            ) from exc

    def bytes(self, url: str, *, timeout: float) -> tuple[bytes, str]:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read(), response.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeNotReady("无法下载本地 runtime 产物") from exc


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _run_cli_command(
    context: ExecutionContext,
    command: list[str],
    *,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """执行真实 CLI；生产路径可取消，注入 runner 仅保留离线单测兼容。

    默认监督器会轮询任务取消标记并终止整棵进程树。现有离线测试仍可注入
    确定性 runner 来检查 argv；该测试缝不会被 registry 的生产构建路径使用。
    """

    context.ensure_not_cancelled()
    if runner is None:
        return run_cancellable_process(
            context,
            command,
            timeout_seconds=timeout_seconds,
            env=env,
        )
    try:
        completed = runner(
            command,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            **({"env": env} if env is not None else {}),
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterRuntimeError(
            "EXECUTION_TIMEOUT",
            "本地 CLI 任务执行超时",
            retryable=True,
            details={"timeout_seconds": timeout_seconds},
        ) from exc
    except OSError as exc:
        raise RuntimeNotReady(
            "本地 CLI 进程无法启动",
            {"executable": Path(command[0]).name, "reason": type(exc).__name__},
        ) from exc
    context.ensure_not_cancelled()
    return completed


def _validate_fixed_request(
    input_data: dict[str, Any],
    *,
    allowed_model_ids: tuple[str, ...],
    workflow_version: str,
    adapter_name: str,
) -> str:
    """真实适配器只接受固定模型白名单和固定 workflow，禁止请求侧换版本。"""

    model_id = str(input_data.get("model_id") or "")
    if allowed_model_ids and model_id not in allowed_model_ids:
        raise RuntimeNotReady(
            f"{adapter_name} model_id 不在固定白名单",
            {"model_id": model_id},
        )
    requested_workflow = str(input_data.get("workflow_version") or "")
    # 旧的离线骨架单测允许空白名单；真实 registry 构建器强制非空白名单，
    # 因而只有显式配置激活的适配器才启用请求版本锁。
    if allowed_model_ids and workflow_version and requested_workflow != workflow_version:
        raise RuntimeNotReady(
            f"{adapter_name} workflow_version 不匹配",
            {"expected": workflow_version, "actual": requested_workflow},
        )
    return model_id


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 120
    allowed_model_ids: tuple[str, ...] = ()
    model_version: str = "unconfigured"
    workflow_version: str = "v1"


class OllamaAdapter:
    """通过 Ollama 的 OpenAI-compatible `/v1/chat/completions` 执行 LLM。"""

    kind = "llm"
    resource_group = "gpu"

    def __init__(
        self,
        config: OllamaConfig,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()

    def capability(self) -> dict[str, Any]:
        return {
            "capability": self.kind,
            "adapter": "ollama",
            "resource_group": self.resource_group,
            "endpoint": "/v1/chat/completions",
            "models": [
                {
                    "model_id": model_id,
                    "version": self.config.model_version,
                    "ready": True,
                }
                for model_id in self.config.allowed_model_ids
            ],
            "workflow_versions": [self.config.workflow_version],
        }

    def execute(self, context: ExecutionContext, input_data: dict[str, Any]) -> dict[str, Any]:
        requested_model_id = _validate_fixed_request(
            input_data,
            allowed_model_ids=self.config.allowed_model_ids,
            workflow_version=self.config.workflow_version,
            adapter_name="Ollama",
        )
        model_id = requested_model_id.removeprefix("ollama/")
        if not model_id:
            raise RuntimeNotReady("Ollama model_id 未配置")
        messages = []
        if input_data.get("negative_prompt"):
            messages.append({"role": "system", "content": input_data["negative_prompt"]})
        messages.append({"role": "user", "content": str(input_data.get("prompt") or "")})
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "stream": False,
        }
        if input_data.get("seed") is not None:
            payload["seed"] = input_data["seed"]
        response = self.transport.json(
            "POST",
            _join_url(self.config.base_url, "/v1/chat/completions"),
            payload=payload,
            timeout=self.config.timeout_seconds,
        )
        try:
            text = str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterRuntimeError(
                "OUTPUT_SCHEMA_INVALID",
                "Ollama 返回缺少 choices[0].message.content",
            ) from exc
        artifact = context.write_artifact("ollama-result.txt", text.encode("utf-8"), "text/plain")
        return {
            "text": text,
            "artifact_ids": [artifact["id"]],
            "metadata": {"adapter": "ollama", "model_id": model_id},
        }


@dataclass(frozen=True, slots=True)
class ComfyUIConfig:
    manifest_path: Path
    workflow_version: str
    base_url: str = "http://127.0.0.1:8188"
    timeout_seconds: float = 180
    poll_interval_seconds: float = 0.25
    allowed_model_ids: tuple[str, ...] = ()
    model_version: str = "unconfigured"


class ComfyUIAdapter:
    """加载版本化 workflow manifest，显式绑定输入并调用 prompt/history/view。"""

    resource_group = "gpu"
    REQUIRED_BINDINGS = ("prompt", "negative_prompt", "width", "height", "seed")

    def __init__(
        self,
        kind: str,
        config: ComfyUIConfig,
        transport: HttpTransport | None = None,
    ) -> None:
        if kind not in {"text2image", "image_edit", "image2video"}:
            raise ValueError("ComfyUIAdapter capability 不受支持")
        self.kind = kind
        self.config = config
        self.transport = transport or UrllibTransport()

    def capability(self) -> dict[str, Any]:
        return {
            "capability": self.kind,
            "adapter": "comfyui",
            "resource_group": self.resource_group,
            "workflow_version": self.config.workflow_version,
            "models": [
                {
                    "model_id": model_id,
                    "version": self.config.model_version,
                    "ready": True,
                }
                for model_id in self.config.allowed_model_ids
            ],
            "workflow_versions": [self.config.workflow_version],
        }

    def load_manifest(self) -> dict[str, Any]:
        path = Path(self.config.manifest_path)
        if not path.is_file():
            raise RuntimeNotReady("ComfyUI workflow manifest 不存在", {"path": str(path)})
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeNotReady("ComfyUI workflow manifest 无法读取") from exc
        if manifest.get("manifest_version") != 1:
            raise RuntimeNotReady("ComfyUI manifest_version 必须为 1")
        if manifest.get("workflow_version") != self.config.workflow_version:
            raise RuntimeNotReady(
                "ComfyUI workflow_version 不匹配",
                {
                    "expected": self.config.workflow_version,
                    "actual": manifest.get("workflow_version"),
                },
            )
        bindings = manifest.get("bindings") or {}
        missing = [name for name in self.REQUIRED_BINDINGS if name not in bindings]
        if missing:
            raise RuntimeNotReady("ComfyUI manifest 缺少显式绑定", {"missing": missing})
        if not isinstance(manifest.get("workflow"), dict):
            raise RuntimeNotReady("ComfyUI manifest 缺少 workflow")
        return manifest

    @staticmethod
    def _bind(workflow: dict[str, Any], binding: dict[str, Any], value: Any) -> None:
        node_id = str(binding.get("node_id") or "")
        input_name = str(binding.get("input_name") or "")
        try:
            inputs = workflow[node_id]["inputs"]
        except (KeyError, TypeError) as exc:
            raise RuntimeNotReady(
                "ComfyUI binding 指向不存在的节点",
                {"node_id": node_id, "input_name": input_name},
            ) from exc
        if not input_name:
            raise RuntimeNotReady("ComfyUI binding 缺少 input_name")
        inputs[input_name] = value

    @staticmethod
    def _artifact_value(item: dict[str, Any] | str) -> str:
        if isinstance(item, str):
            return item
        for key in ("filename", "path", "uri"):
            if item.get(key):
                return str(item[key])
        raise AdapterRuntimeError("INPUT_INVALID", "input_artifact 缺少 filename/path/uri")

    def bind_workflow(self, input_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = self.load_manifest()
        workflow = copy.deepcopy(manifest["workflow"])
        output_spec = input_data.get("output_spec") or {}
        values = {
            "prompt": str(input_data.get("prompt") or ""),
            "negative_prompt": str(input_data.get("negative_prompt") or ""),
            "width": int(output_spec.get("width", 1024)),
            "height": int(output_spec.get("height", 1024)),
            "seed": int(input_data.get("seed") if input_data.get("seed") is not None else 0),
        }
        for name, value in values.items():
            self._bind(workflow, manifest["bindings"][name], value)

        input_bindings = manifest["bindings"].get("input_artifacts", [])
        artifacts = input_data.get("input_artifacts") or []
        if len(artifacts) > len(input_bindings):
            raise AdapterRuntimeError(
                "INPUT_INVALID",
                "input_artifacts 数量超过 manifest 显式绑定数量",
            )
        for index, artifact in enumerate(artifacts):
            self._bind(workflow, input_bindings[index], self._artifact_value(artifact))
        return workflow, manifest

    def execute(self, context: ExecutionContext, input_data: dict[str, Any]) -> dict[str, Any]:
        _validate_fixed_request(
            input_data,
            allowed_model_ids=self.config.allowed_model_ids,
            workflow_version=self.config.workflow_version,
            adapter_name="ComfyUI",
        )
        workflow, manifest = self.bind_workflow(input_data)
        submitted = self.transport.json(
            "POST",
            _join_url(self.config.base_url, "/prompt"),
            payload={"prompt": workflow, "client_id": str(uuid.uuid4())},
            timeout=self.config.timeout_seconds,
        )
        prompt_id = str(submitted.get("prompt_id") or "")
        if not prompt_id:
            raise AdapterRuntimeError("OUTPUT_SCHEMA_INVALID", "ComfyUI 未返回 prompt_id")

        deadline = time.monotonic() + self.config.timeout_seconds
        history_item: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            context.ensure_not_cancelled()
            history = self.transport.json(
                "GET",
                _join_url(self.config.base_url, f"/history/{prompt_id}"),
                timeout=self.config.timeout_seconds,
            )
            history_item = history.get(prompt_id)
            if history_item:
                break
            context.sleep(max(1, int(self.config.poll_interval_seconds * 1000)))
        if not history_item:
            raise AdapterRuntimeError("EXECUTION_TIMEOUT", "ComfyUI 任务等待超时", retryable=True)

        artifact_ids = []
        outputs = history_item.get("outputs") or {}
        for output_binding in manifest.get("outputs", []):
            node_id = str(output_binding.get("node_id") or "")
            collection = str(output_binding.get("collection") or "images")
            items = (outputs.get(node_id) or {}).get(collection) or []
            for item in items:
                query = urllib.parse.urlencode(
                    {
                        "filename": item.get("filename", ""),
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    }
                )
                content, media_type = self.transport.bytes(
                    _join_url(self.config.base_url, f"/view?{query}"),
                    timeout=self.config.timeout_seconds,
                )
                artifact = context.write_artifact(
                    str(item.get("filename") or f"comfyui-{uuid.uuid4().hex}.bin"),
                    content,
                    media_type,
                    metadata={
                        "width": int((input_data.get("output_spec") or {}).get("width", 0) or 0),
                        "height": int((input_data.get("output_spec") or {}).get("height", 0) or 0),
                        "workflow_version": self.config.workflow_version,
                    },
                )
                artifact_ids.append(artifact["id"])
        if not artifact_ids:
            raise AdapterRuntimeError("OUTPUT_SCHEMA_INVALID", "ComfyUI history 中没有可下载产物")
        return {
            "artifact_ids": artifact_ids,
            "metadata": {
                "adapter": "comfyui",
                "prompt_id": prompt_id,
                "workflow_version": self.config.workflow_version,
            },
        }


@dataclass(frozen=True, slots=True)
class LightX2VConfig:
    command_template: tuple[str, ...]
    model_path: Path
    timeout_seconds: float = 900
    allowed_model_ids: tuple[str, ...] = ()
    model_version: str = "unconfigured"
    workflow_version: str = "v1"


class LightX2VAdapter:
    """使用固定 argv 模板启动 LightX2V，明确禁止 shell=True。"""

    kind = "image2video"
    resource_group = "gpu"
    ALLOWED_PLACEHOLDERS = {
        "prompt",
        "negative_prompt",
        "seed",
        "model_path",
        "input_path",
        "output_path",
    }

    def __init__(
        self,
        config: LightX2VConfig,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner

    def capability(self) -> dict[str, Any]:
        return {
            "capability": self.kind,
            "adapter": "lightx2v",
            "resource_group": self.resource_group,
            "models": [
                {
                    "model_id": model_id,
                    "version": self.config.model_version,
                    "ready": True,
                }
                for model_id in self.config.allowed_model_ids
            ],
            "workflow_versions": [self.config.workflow_version],
        }

    def build_command(self, input_data: dict[str, Any], output_path: Path) -> list[str]:
        artifacts = input_data.get("input_artifacts") or []
        input_path = ComfyUIAdapter._artifact_value(artifacts[0]) if artifacts else ""
        values = {
            "prompt": str(input_data.get("prompt") or ""),
            "negative_prompt": str(input_data.get("negative_prompt") or ""),
            "seed": str(input_data.get("seed") if input_data.get("seed") is not None else 0),
            "model_path": str(self.config.model_path),
            "input_path": input_path,
            "output_path": str(output_path),
        }
        command = []
        for token in self.config.command_template:
            for _, placeholder, format_spec, conversion in string.Formatter().parse(token):
                if placeholder and (
                    placeholder not in self.ALLOWED_PLACEHOLDERS
                    or format_spec
                    or conversion
                ):
                    raise RuntimeNotReady(
                        "LightX2V command_template 包含未允许占位符",
                        {"placeholder": placeholder},
                    )
            try:
                command.append(token.format_map(values))
            except KeyError as exc:
                raise RuntimeNotReady(
                    "LightX2V command_template 包含未允许占位符",
                    {"placeholder": str(exc)},
                ) from exc
        return command

    def execute(self, context: ExecutionContext, input_data: dict[str, Any]) -> dict[str, Any]:
        _validate_fixed_request(
            input_data,
            allowed_model_ids=self.config.allowed_model_ids,
            workflow_version=self.config.workflow_version,
            adapter_name="LightX2V",
        )
        if not self.config.command_template:
            raise RuntimeNotReady("LightX2V command_template 未配置")
        executable = self.config.command_template[0]
        if not (shutil.which(executable) or Path(executable).is_file()):
            raise RuntimeNotReady("LightX2V 可执行程序不存在", {"executable": executable})
        if not Path(self.config.model_path).exists():
            raise RuntimeNotReady("LightX2V 模型目录不存在")

        work_root = context.artifacts.root / ".work"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{context.job_id}-", dir=work_root) as raw_work:
            output_path = Path(raw_work) / "lightx2v-output.mp4"
            command = self.build_command(input_data, output_path)
            completed = _run_cli_command(
                context,
                command,
                timeout_seconds=self.config.timeout_seconds,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                runner=self.runner,
            )
            if completed.returncode != 0:
                raise AdapterRuntimeError(
                    "RUNTIME_PROCESS_FAILED",
                    "LightX2V 进程执行失败",
                    details={"returncode": completed.returncode},
                )
            if not output_path.is_file():
                raise AdapterRuntimeError("OUTPUT_MISSING", "LightX2V 未生成输出文件")
            artifact = context.write_artifact(
                "lightx2v-output.mp4",
                output_path.read_bytes(),
                "video/mp4",
                metadata=self._artifact_metadata(input_data),
            )
        return {"artifact_ids": [artifact["id"]], "metadata": {"adapter": "lightx2v"}}

    @staticmethod
    def _artifact_metadata(input_data: dict[str, Any]) -> dict[str, Any]:
        spec = input_data.get("output_spec") or {}
        width = int(spec.get("width", 0) or 0)
        height = int(spec.get("height", 0) or 0)
        resolution = str(spec.get("resolution") or "")
        if (not width or not height) and "x" in resolution.lower():
            raw_width, raw_height = resolution.lower().split("x", 1)
            if raw_width.isdigit() and raw_height.isdigit():
                width, height = int(raw_width), int(raw_height)
        return {
            "duration_seconds": float(spec.get("duration", 0) or 0),
            "fps": int(spec.get("fps", 24) or 24),
            "width": width,
            "height": height,
        }


@dataclass(frozen=True, slots=True)
class FFmpegMotionConfig:
    executable: str = "ffmpeg"
    timeout_seconds: float = 300
    max_segment_seconds: float = 5.0
    overlap_frames: int = 8
    allowed_model_ids: tuple[str, ...] = ()
    model_version: str = "unconfigured"
    workflow_version: str = "v1"


class FFmpegMotionAdapter:
    """本地 zoom/pan/crossfade 运动渲染，并生成短分段 overlap 计划。"""

    kind = "motion_render"
    resource_group = "cpu_motion"
    MODES = {"zoom", "pan", "crossfade", "compose"}

    def __init__(
        self,
        config: FFmpegMotionConfig = FFmpegMotionConfig(),
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner

    def capability(self) -> dict[str, Any]:
        return {
            "capability": self.kind,
            "adapter": "ffmpeg_motion",
            "resource_group": self.resource_group,
            "modes": sorted(self.MODES),
            "max_segment_seconds": self.config.max_segment_seconds,
            "overlap_frames": self.config.overlap_frames,
            "models": [
                {
                    "model_id": model_id,
                    "version": self.config.model_version,
                    "ready": True,
                }
                for model_id in self.config.allowed_model_ids
            ],
            "workflow_versions": [self.config.workflow_version],
        }

    def build_plan(self, input_data: dict[str, Any]) -> dict[str, Any]:
        spec = input_data.get("output_spec") or {}
        mode = str(spec.get("motion", "zoom"))
        if mode not in self.MODES:
            raise AdapterRuntimeError("INPUT_INVALID", f"不支持的 motion: {mode}")
        duration = float(spec.get("duration", 5.0))
        fps = int(spec.get("fps", 24))
        width = int(spec.get("width", 1280))
        height = int(spec.get("height", 720))
        if duration <= 0 or fps < 8 or width <= 0 or height <= 0:
            raise AdapterRuntimeError("INPUT_INVALID", "duration/fps/width/height 无效")
        overlap_seconds = self.config.overlap_frames / fps
        if mode == "compose":
            artifacts = input_data.get("input_artifacts") or []
            if not artifacts:
                raise AdapterRuntimeError("INPUT_INVALID", "compose 至少需要一个视频分段")
            input_durations = []
            for item in artifacts:
                duration_value = item.get("duration_seconds", 0) if isinstance(item, dict) else 0
                duration_value = float(duration_value or 0)
                if duration_value <= 0:
                    raise AdapterRuntimeError(
                        "INPUT_INVALID", "compose 输入缺少已校验的 duration_seconds"
                    )
                input_durations.append(duration_value)
            natural_duration = sum(input_durations) - overlap_seconds * max(0, len(artifacts) - 1)
            if natural_duration + 0.05 < duration:
                raise AdapterRuntimeError(
                    "INPUT_INVALID", "视频分段总时长不足以合成目标时长"
                )
            return {
                "mode": mode,
                "duration_seconds": duration,
                "fps": fps,
                "width": width,
                "height": height,
                "overlap_frames": self.config.overlap_frames,
                "overlap_seconds": overlap_seconds,
                "input_durations": input_durations,
                "natural_duration_seconds": natural_duration,
                "trim_exact_duration": bool(spec.get("trim_exact_duration", True)),
                "segments": [],
            }
        step = self.config.max_segment_seconds - overlap_seconds
        segments = []
        start = 0.0
        index = 0
        while start < duration:
            segment_duration = min(self.config.max_segment_seconds, duration - start)
            segments.append(
                {
                    "index": index,
                    "start_seconds": round(start, 6),
                    "duration_seconds": round(segment_duration, 6),
                    "overlap_frames": self.config.overlap_frames if index else 0,
                    "overlap_seconds": round(overlap_seconds if index else 0, 6),
                }
            )
            if start + segment_duration >= duration:
                break
            start += step
            index += 1
        return {
            "mode": mode,
            "duration_seconds": duration,
            "fps": fps,
            "width": width,
            "height": height,
            "segments": segments,
        }

    def _build_command(
        self,
        input_paths: list[str],
        output_path: Path,
        plan: dict[str, Any],
    ) -> list[str]:
        command = [self.config.executable, "-y"]
        is_compose = plan["mode"] == "compose"
        for input_path in input_paths:
            if is_compose:
                command.extend(["-i", input_path])
            else:
                command.extend(["-loop", "1", "-i", input_path])
        fps = plan["fps"]
        width = plan["width"]
        height = plan["height"]
        duration = min(plan["duration_seconds"], self.config.max_segment_seconds)
        mode = plan["mode"]
        if mode == "compose":
            # 每个分段先归一化分辨率、帧率与时间基，再按真实分段时长计算
            # xfade offset。最终 -t 精确裁剪到用户选择的 8–10 秒，避免累计
            # 浮点误差或模型尾帧冗余改变成片时长。
            filters = [
                f"[{index}:v]scale={width}:{height},fps={fps},"
                f"settb=AVTB,setpts=PTS-STARTPTS[v{index}]"
                for index in range(len(input_paths))
            ]
            output_label = "v0"
            combined_duration = float(plan["input_durations"][0])
            overlap = float(plan["overlap_seconds"])
            for index in range(1, len(input_paths)):
                next_label = f"vx{index}"
                offset = max(0.0, combined_duration - overlap)
                filters.append(
                    f"[{output_label}][v{index}]xfade=transition=fade:"
                    f"duration={overlap:.6f}:offset={offset:.6f}[{next_label}]"
                )
                output_label = next_label
                combined_duration += float(plan["input_durations"][index]) - overlap
            command.extend(["-filter_complex", ";".join(filters), "-map", f"[{output_label}]"])
            duration = float(plan["duration_seconds"])
        elif mode == "zoom":
            filter_value = (
                f"zoompan=z='min(zoom+0.0015,1.15)':d={max(1, int(duration * fps))}:"
                f"s={width}x{height}:fps={fps}"
            )
            command.extend(["-vf", filter_value])
        elif mode == "pan":
            filter_value = (
                f"scale={width + 160}:{height},crop={width}:{height}:"
                f"x='min((iw-ow)*t/{max(duration, 0.001)},iw-ow)':y=0,fps={fps}"
            )
            command.extend(["-vf", filter_value])
        else:
            if len(input_paths) < 2:
                raise AdapterRuntimeError("INPUT_INVALID", "crossfade 至少需要两个输入产物")
            overlap = self.config.overlap_frames / fps
            offset = max(0.0, duration - overlap)
            command.extend(
                [
                    "-filter_complex",
                    f"[0:v][1:v]xfade=transition=fade:duration={overlap}:offset={offset},"
                    f"scale={width}:{height},fps={fps}",
                ]
            )
        command.extend(
            [
                "-t",
                str(duration),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
        )
        return command

    def execute(self, context: ExecutionContext, input_data: dict[str, Any]) -> dict[str, Any]:
        _validate_fixed_request(
            input_data,
            allowed_model_ids=self.config.allowed_model_ids,
            workflow_version=self.config.workflow_version,
            adapter_name="FFmpeg",
        )
        if not (shutil.which(self.config.executable) or Path(self.config.executable).is_file()):
            raise RuntimeNotReady("FFmpeg 可执行程序不存在")
        artifacts = input_data.get("input_artifacts") or []
        input_paths = [ComfyUIAdapter._artifact_value(item) for item in artifacts]
        if not input_paths or any(not Path(path).is_file() for path in input_paths):
            raise RuntimeNotReady("FFmpeg motion 输入文件未准备好")
        plan = self.build_plan(input_data)
        work_root = context.artifacts.root / ".work"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{context.job_id}-", dir=work_root) as raw_work:
            output_path = Path(raw_work) / "motion-render.mp4"
            command = self._build_command(input_paths, output_path, plan)
            completed = _run_cli_command(
                context,
                command,
                timeout_seconds=self.config.timeout_seconds,
                runner=self.runner,
            )
            if completed.returncode != 0:
                raise AdapterRuntimeError(
                    "RUNTIME_PROCESS_FAILED",
                    "FFmpeg motion 执行失败",
                    details={"returncode": completed.returncode},
                )
            if not output_path.is_file():
                raise AdapterRuntimeError("OUTPUT_MISSING", "FFmpeg 未生成输出文件")
            artifact = context.write_artifact(
                "motion-render.mp4",
                output_path.read_bytes(),
                "video/mp4",
                metadata={
                    "duration_seconds": plan["duration_seconds"],
                    "fps": plan["fps"],
                    "width": plan["width"],
                    "height": plan["height"],
                    "mode": plan["mode"],
                },
            )
        return {
            "artifact_ids": [artifact["id"]],
            "metadata": {"adapter": "ffmpeg_motion", "plan": plan},
        }
