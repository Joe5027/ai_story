"""可离线验证的 Mock 运行时适配器。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .artifacts import ArtifactStore
from .errors import AdapterExecutionError, CancelledExecution, RuntimeNotReady
from .journal import JobJournal


class Adapter(Protocol):
    kind: str
    resource_group: str

    def execute(self, context: "ExecutionContext", input_data: dict[str, Any]) -> dict[str, Any]: ...

    def capability(self) -> dict[str, Any]: ...


@dataclass(slots=True)
class ExecutionContext:
    """向适配器暴露最小的取消检查和产物写入能力。"""

    job_id: str
    journal: JobJournal
    artifacts: ArtifactStore

    def ensure_not_cancelled(self) -> None:
        if self.journal.is_cancel_requested(self.job_id):
            raise CancelledExecution("任务已取消")

    def sleep(self, milliseconds: int) -> None:
        remaining = max(0, milliseconds) / 1000
        while remaining > 0:
            self.ensure_not_cancelled()
            step = min(0.01, remaining)
            time.sleep(step)
            remaining -= step
        self.ensure_not_cancelled()

    def write_artifact(
        self,
        name: str,
        content: bytes,
        media_type: str = "application/json",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_not_cancelled()
        return self.artifacts.write(
            job_id=self.job_id,
            name=name,
            content=content,
            media_type=media_type,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class MockAdapter:
    """以确定性 JSON 产物模拟一种模型或渲染能力。"""

    kind: str
    resource_group: str
    description: str

    def capability(self) -> dict[str, Any]:
        payload = {
            "capability": self.kind,
            "adapter": "mock",
            "resource_group": self.resource_group,
            "models": [
                {"model_id": "mock/default", "version": "mock-v1", "ready": True}
            ],
            "profiles": ["draft", "balanced", "final"],
            "workflow_versions": ["v1"],
            "description": self.description,
            "artifact_media_types": ["application/json"],
            "test_controls": {
                "delay_ms": "0..5000",
                "fail": "布尔值；为 true 时产生稳定失败状态",
            },
        }
        if self.kind in {"text2image", "image_edit"}:
            payload["supported_resolutions"] = ["768x768", "1024x1024", "1280x720"]
        if self.kind == "image2video":
            payload["supported_resolutions"] = ["854x480", "1280x720"]
            payload["native_max_duration_seconds"] = 5
            payload["fps"] = [24]
        if self.kind == "motion_render":
            payload["supported_resolutions"] = ["1280x720", "720x1280"]
            payload["native_max_duration_seconds"] = 10
            payload["fps"] = [24]
        return payload

    def execute(self, context: ExecutionContext, input_data: dict[str, Any]) -> dict[str, Any]:
        model_id = str(input_data.get("model_id") or "")
        if not model_id.startswith("mock"):
            raise RuntimeNotReady(
                "真实适配器尚未在当前 registry 激活，拒绝静默使用 Mock",
                {"model_id": model_id, "capability": self.kind},
            )
        delay_ms = input_data.get("delay_ms", 10)
        if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 0 <= delay_ms <= 5000:
            raise AdapterExecutionError("delay_ms 必须是 0 到 5000 的整数")
        context.sleep(delay_ms)
        if input_data.get("fail") is True:
            raise AdapterExecutionError(f"{self.kind} mock requested failure")

        payload = {
            "adapter": "mock",
            "capability": self.kind,
            "job_id": context.job_id,
            "input": input_data,
            "summary": self._summary(input_data),
        }
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        artifact = context.write_artifact(
            f"{self.kind}-result.json",
            content,
            "application/json",
        )
        result = {
            "adapter": "mock",
            "capability": self.kind,
            "artifact_ids": [artifact["id"]],
            "sha256": artifact["sha256"],
            "metadata": {"mock": True, "resource_group": self.resource_group},
        }
        if self.kind == "llm":
            result["text"] = payload["summary"]
        return result

    def _summary(self, input_data: dict[str, Any]) -> str:
        if self.kind == "llm":
            return f"mock completion: {input_data.get('prompt', '')}".strip()
        if self.kind == "text2image":
            return "mock image manifest created"
        if self.kind == "image_edit":
            return "mock edited-image manifest created"
        if self.kind == "image2video":
            return "mock video manifest created"
        return "mock motion-render manifest created"


def build_mock_registry() -> dict[str, MockAdapter]:
    """构建每次 runtime reload 都可重新生成的适配器注册表。"""

    adapters = [
        MockAdapter("llm", "gpu", "离线模拟文本生成"),
        MockAdapter("text2image", "gpu", "离线模拟文生图"),
        MockAdapter("image_edit", "gpu", "离线模拟图片编辑"),
        MockAdapter("image2video", "gpu", "离线模拟图生视频"),
        MockAdapter("motion_render", "cpu_motion", "离线模拟运镜渲染"),
    ]
    return {adapter.kind: adapter for adapter in adapters}
