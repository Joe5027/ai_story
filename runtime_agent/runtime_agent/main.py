"""FastAPI v1 应用入口。"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adapters import Adapter
from .artifacts import ArtifactStore
from .auth import BearerAuthenticator
from .config import RuntimeSettings
from .errors import AgentError
from .journal import JobJournal
from .hardware import hardware_snapshot
from .models import Capability, JobCreateRequest, JobStatus, RuntimeReloadRequest
from .registry import RuntimeRegistryConfigError, build_runtime_registry
from .scheduler import RuntimeScheduler


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    retryable: bool | None = None,
) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": status_code >= 500 if retryable is None else retryable,
                "details": jsonable_encoder(details),
            },
            "request_id": _request_id(request),
        },
    )


def _capabilities_payload(
    registry: dict[str, Adapter],
    settings: RuntimeSettings,
    generation: int,
    resource_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "runtime_generation": generation,
        "service_version": settings.service_version,
        "hardware": hardware_snapshot(settings.artifacts_dir),
        "resource_groups": resource_groups or [
            {"name": name, "capacity": capacity, "in_use": 0, "available": capacity}
            for name, capacity in sorted(settings.resource_capacities.items())
        ],
        "adapters": [registry[kind].capability() for kind in sorted(registry)],
    }


def _artifact_view(artifact: dict[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata") or {}
    return {
        "id": artifact["id"],
        "artifact_id": artifact["id"],
        "job_id": artifact["job_id"],
        "name": artifact["name"],
        "filename": artifact["name"],
        "media_type": artifact["media_type"],
        "content_type": artifact["media_type"],
        "sha256": artifact["sha256"],
        "size_bytes": artifact["size_bytes"],
        "file_size": artifact["size_bytes"],
        "metadata": metadata,
        "width": int(metadata.get("width") or 0),
        "height": int(metadata.get("height") or 0),
        "duration_seconds": float(metadata.get("duration_seconds") or 0),
        "fps": float(metadata.get("fps") or 0),
        "created_at": artifact["created_at"],
        "download_url": f"/v1/artifacts/{artifact['id']}",
    }


def _job_view(journal: JobJournal, job: dict[str, Any]) -> dict[str, Any]:
    """把内部 journal 行映射为 Django client 使用的顶层响应。"""

    artifacts = [_artifact_view(item) for item in journal.list_artifacts(job["id"])]
    return {
        "id": job["id"],
        "job_id": job["id"],
        "status": job["status"],
        "capability": job["kind"],
        "result": job["result"],
        "artifacts": artifacts,
        "error": job["error"],
        "cancel_requested": job["cancel_requested"],
        "runtime_generation": job["runtime_generation"],
        "workflow_version": job["metadata"].get("workflow_version"),
        "project_id": job["metadata"].get("project_id"),
        "stage_type": job["metadata"].get("stage_type"),
        "work_item_id": job["metadata"].get("work_item_id"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def create_app(settings: RuntimeSettings | None = None) -> FastAPI:
    """构造可注入临时目录和鉴权策略的 FastAPI 应用。"""

    runtime_settings = settings or RuntimeSettings.from_env()
    journal = JobJournal(runtime_settings.db_path)
    journal.initialize()
    artifacts = ArtifactStore(runtime_settings.artifacts_dir, journal)
    artifacts.initialize()
    registry = build_runtime_registry(runtime_settings.config_path)
    scheduler = RuntimeScheduler(
        journal=journal,
        artifacts=artifacts,
        registry=registry,
        capacities=runtime_settings.resource_capacities,
        poll_interval_seconds=runtime_settings.poll_interval_seconds,
    )
    generation_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.recovery = scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(
        title="AI Story Runtime Agent",
        version=runtime_settings.service_version,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.journal = journal
    app.state.artifacts = artifacts
    app.state.scheduler = scheduler
    app.state.runtime_generation = journal.current_runtime_generation()
    app.state.generation_lock = generation_lock
    authenticate = BearerAuthenticator(runtime_settings)

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "").strip()
        request.state.request_id = supplied[:128] if supplied else str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(AgentError)
    async def handle_agent_error(request: Request, exc: AgentError):
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            retryable=exc.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return _error_response(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="请求参数校验失败",
            details=exc.errors(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        message = "请求的资源不存在" if exc.status_code == 404 else str(exc.detail)
        return _error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=message,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        return _error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="运行时代理发生内部错误",
        )

    @app.get("/v1/health/live", tags=["health"])
    @app.get("/v1/live", tags=["health"], include_in_schema=False)
    async def live() -> dict[str, Any]:
        """仅证明 HTTP 进程存活；按契约不要求鉴权。"""

        return {
            "status": "live",
            "service": runtime_settings.service_name,
            "version": runtime_settings.service_version,
        }

    @app.get("/v1/health/ready", tags=["health"], dependencies=[Depends(authenticate)])
    @app.get(
        "/v1/ready",
        tags=["health"],
        dependencies=[Depends(authenticate)],
        include_in_schema=False,
    )
    async def ready() -> JSONResponse:
        checks = {
            "journal": journal.healthcheck(),
            "artifacts": artifacts.healthcheck(),
            "scheduler": scheduler.is_alive(),
            "runtime_registry": bool(scheduler.registry_snapshot()),
        }
        is_ready = all(checks.values())
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={
                "status": "ready" if is_ready else "not_ready",
                "checks": checks,
                "queue": journal.queue_snapshot(),
                "resource_groups": scheduler.resource_snapshot(),
            },
        )

    @app.get(
        "/v1/capabilities",
        tags=["runtime"],
        dependencies=[Depends(authenticate)],
    )
    async def capabilities() -> dict[str, Any]:
        with generation_lock:
            generation = app.state.runtime_generation
            snapshot = scheduler.registry_snapshot()
        return _capabilities_payload(
            snapshot, runtime_settings, generation, scheduler.resource_snapshot()
        )

    @app.post("/v1/jobs", tags=["jobs"], dependencies=[Depends(authenticate)])
    async def create_job(
        body: JobCreateRequest,
        idempotency_key: str = Header(..., alias="Idempotency-Key"),
    ) -> JSONResponse:
        normalized_key = idempotency_key.strip()
        if not normalized_key or len(normalized_key) > 200:
            raise AgentError(
                400,
                "INVALID_IDEMPOTENCY_KEY",
                "Idempotency-Key 长度必须为 1 到 200",
            )
        request_data = body.model_dump(mode="json", by_alias=True)
        request_hash = _canonical_hash(request_data)
        adapter = scheduler.registry_snapshot().get(body.capability.value)
        if adapter is None:
            raise AgentError(
                422,
                "ADAPTER_NOT_FOUND",
                "请求的能力未注册",
                {"capability": body.capability.value},
            )
        with generation_lock:
            generation = app.state.runtime_generation
        job, replay = journal.create_or_get_job(
            idempotency_key=normalized_key,
            request_hash=request_hash,
            kind=body.capability.value,
            input_data=body.adapter_input(),
            metadata=body.journal_metadata(),
            resource_group=adapter.resource_group,
            runtime_generation=generation,
        )
        scheduler.wake()
        return JSONResponse(
            status_code=200 if replay else 202,
            headers={"Idempotent-Replay": "true" if replay else "false"},
            content={
                "id": job["id"],
                "job_id": job["id"],
                "status": job["status"],
                "idempotent_replay": replay,
            },
        )

    @app.get("/v1/jobs", tags=["jobs"], dependencies=[Depends(authenticate)])
    async def list_jobs(
        status: JobStatus | None = Query(default=None),
        capability: Capability | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        jobs = journal.list_jobs(
            status=status.value if status else None,
            kind=capability.value if capability else None,
            limit=limit,
        )
        return {"jobs": [_job_view(journal, job) for job in jobs], "count": len(jobs)}

    @app.get("/v1/jobs/{job_id}", tags=["jobs"], dependencies=[Depends(authenticate)])
    async def get_job(job_id: str) -> dict[str, Any]:
        return _job_view(journal, journal.get_job(job_id))

    @app.delete(
        "/v1/jobs/{job_id}",
        tags=["jobs"],
        dependencies=[Depends(authenticate)],
        status_code=202,
    )
    async def delete_job(job_id: str) -> JSONResponse:
        job = journal.request_cancel(job_id)
        scheduler.wake()
        return JSONResponse(status_code=202, content=_job_view(journal, job))

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        tags=["jobs"],
        dependencies=[Depends(authenticate)],
        include_in_schema=False,
    )
    async def cancel_job(job_id: str) -> JSONResponse:
        job = journal.request_cancel(job_id)
        scheduler.wake()
        return JSONResponse(status_code=202, content=_job_view(journal, job))

    @app.get(
        "/v1/jobs/{job_id}/artifacts",
        tags=["artifacts"],
        dependencies=[Depends(authenticate)],
    )
    async def list_artifacts(job_id: str) -> dict[str, Any]:
        items = [_artifact_view(item) for item in journal.list_artifacts(job_id)]
        return {"artifacts": items, "count": len(items)}

    @app.get(
        "/v1/artifacts/{artifact_id}/metadata",
        tags=["artifacts"],
        dependencies=[Depends(authenticate)],
    )
    async def get_artifact_metadata(artifact_id: str) -> dict[str, Any]:
        return {"artifact": _artifact_view(journal.get_artifact(artifact_id))}

    @app.get(
        "/v1/artifacts/{artifact_id}",
        tags=["artifacts"],
        dependencies=[Depends(authenticate)],
    )
    async def download_artifact(artifact_id: str) -> FileResponse:
        artifact = journal.get_artifact(artifact_id)
        path = artifacts.verified_path(artifact)
        return FileResponse(
            path,
            media_type=artifact["media_type"],
            filename=artifact["name"],
            headers={"X-Artifact-SHA256": artifact["sha256"]},
        )

    @app.get(
        "/v1/artifacts/{artifact_id}/download",
        tags=["artifacts"],
        dependencies=[Depends(authenticate)],
        include_in_schema=False,
    )
    async def download_artifact_compatibility(artifact_id: str) -> FileResponse:
        return await download_artifact(artifact_id)

    @app.post(
        "/v1/runtime-reloads",
        tags=["runtime"],
        dependencies=[Depends(authenticate)],
        status_code=201,
    )
    async def reload_runtime(body: RuntimeReloadRequest) -> dict[str, Any]:
        """从同一固定 TOML 完整重建 registry，失败时保留当前 generation。"""

        try:
            new_registry = build_runtime_registry(runtime_settings.config_path)
        except RuntimeRegistryConfigError as exc:
            raise AgentError(
                409,
                "RUNTIME_CONFIG_INVALID",
                "固定 Runtime Agent 配置校验失败，现有 registry 未变更",
                {"reason": str(exc)},
            ) from exc
        with generation_lock:
            next_capabilities = _capabilities_payload(
                new_registry,
                runtime_settings,
                app.state.runtime_generation + 1,
                scheduler.resource_snapshot(),
            )
            capabilities_hash = _canonical_hash(next_capabilities)
            reload_record = journal.record_runtime_reload(
                reason=body.reason,
                capabilities_hash=capabilities_hash,
            )
            scheduler.replace_registry(new_registry)
            app.state.runtime_generation = reload_record["generation"]
        return {
            "reload": reload_record,
            "capabilities": _capabilities_payload(
                new_registry,
                runtime_settings,
                reload_record["generation"],
                scheduler.resource_snapshot(),
            ),
        }

    @app.get(
        "/v1/runtime-reloads",
        tags=["runtime"],
        dependencies=[Depends(authenticate)],
    )
    async def list_runtime_reloads(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        reloads = journal.list_runtime_reloads(limit)
        return {"reloads": reloads, "count": len(reloads)}

    return app
