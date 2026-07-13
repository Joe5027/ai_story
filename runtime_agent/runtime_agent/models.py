"""HTTP 请求模型与运行状态枚举。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Capability(str, Enum):
    LLM = "llm"
    TEXT2IMAGE = "text2image"
    IMAGE_EDIT = "image_edit"
    IMAGE2VIDEO = "image2video"
    MOTION_RENDER = "motion_render"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}


class JobCreateRequest(BaseModel):
    """创建 Mock 执行任务的统一请求。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    capability: Capability
    model_id: str = Field(min_length=1, max_length=200)
    profile: str | None = Field(default=None, max_length=100)
    prompt: str = Field(default="", max_length=200_000)
    negative_prompt: str | None = Field(default=None, max_length=100_000)
    input_artifacts: list[dict[str, Any] | str] = Field(default_factory=list)
    output_spec: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    workflow_version: str = Field(default="v1", min_length=1, max_length=100)
    project_id: str | None = Field(default=None, max_length=200)
    stage_type: str | None = Field(default=None, max_length=100)
    work_item_id: str | None = Field(default=None, max_length=200)

    def adapter_input(self) -> dict[str, Any]:
        """把 Django 契约字段转换为适配器内部输入，不改变幂等哈希原文。"""

        adapter_input = {
            "model_id": self.model_id,
            "profile": self.profile,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "input_artifacts": self.input_artifacts,
            "output_spec": self.output_spec,
            "seed": self.seed,
            "workflow_version": self.workflow_version,
        }
        mock_controls = self.output_spec.get("mock", {})
        if isinstance(mock_controls, dict):
            if "delay_ms" in mock_controls:
                adapter_input["delay_ms"] = mock_controls["delay_ms"]
            if "fail" in mock_controls:
                adapter_input["fail"] = mock_controls["fail"]
        return adapter_input

    def journal_metadata(self) -> dict[str, Any]:
        """保存跨系统关联字段，供恢复和审计使用。"""

        return {
            "workflow_version": self.workflow_version,
            "project_id": self.project_id,
            "stage_type": self.stage_type,
            "work_item_id": self.work_item_id,
        }


class RuntimeReloadRequest(BaseModel):
    """请求重建运行时能力注册表。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="manual", min_length=1, max_length=200)
