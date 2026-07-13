"""进程内调度器，按资源组容量执行持久化任务。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .adapters import Adapter, ExecutionContext
from .artifacts import ArtifactStore
from .errors import AdapterExecutionError, AdapterRuntimeError, CancelledExecution
from .journal import JobJournal


class RuntimeScheduler:
    """轮询 SQLite 队列，并用信号量严格限制 gpu/cpu_motion 并发。"""

    def __init__(
        self,
        *,
        journal: JobJournal,
        artifacts: ArtifactStore,
        registry: dict[str, Adapter],
        capacities: dict[str, int],
        poll_interval_seconds: float,
    ) -> None:
        self.journal = journal
        self.artifacts = artifacts
        self.capacities = dict(capacities)
        self.poll_interval_seconds = poll_interval_seconds
        self._registry = dict(registry)
        self._registry_lock = threading.RLock()
        self._semaphores = {
            name: threading.BoundedSemaphore(value)
            for name, value in self.capacities.items()
        }
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._state_lock = threading.Lock()
        self._last_error: str | None = None

    def start(self) -> dict[str, int]:
        """恢复中断任务并启动后台调度线程。"""

        with self._state_lock:
            if self._dispatcher and self._dispatcher.is_alive():
                return {"requeued": 0, "cancelled": 0}
            recovery = self.journal.recover_interrupted_jobs()
            self._stop_event.clear()
            self._executor = ThreadPoolExecutor(
                max_workers=sum(self.capacities.values()),
                thread_name_prefix="runtime-agent-worker",
            )
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                name="runtime-agent-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()
            return recovery

    def stop(self) -> None:
        """停止接收新任务，并等待当前 Mock 任务安全收尾。"""

        with self._state_lock:
            dispatcher = self._dispatcher
            executor = self._executor
            self._stop_event.set()
            self._wake_event.set()
        if dispatcher:
            dispatcher.join(timeout=5)
        if executor:
            executor.shutdown(wait=True, cancel_futures=False)
        with self._state_lock:
            self._dispatcher = None
            self._executor = None

    def is_alive(self) -> bool:
        return bool(self._dispatcher and self._dispatcher.is_alive())

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def wake(self) -> None:
        self._wake_event.set()

    def replace_registry(self, registry: dict[str, Adapter]) -> None:
        """原子替换能力注册表；已运行任务继续使用原适配器对象。"""

        with self._registry_lock:
            self._registry = dict(registry)
        self.wake()

    def registry_snapshot(self) -> dict[str, Adapter]:
        with self._registry_lock:
            return dict(self._registry)

    def resource_snapshot(self) -> list[dict[str, int | str]]:
        """把持久化 running 数映射为当前槽位，供健康与调度控制面查看。"""

        queue = self.journal.queue_snapshot()
        running = {
            item["resource_group"]: item["count"]
            for item in queue["resource_counts"]
            if item["status"] == "running"
        }
        return [
            {
                "name": name,
                "capacity": capacity,
                "in_use": min(capacity, int(running.get(name, 0))),
                "available": max(0, capacity - int(running.get(name, 0))),
            }
            for name, capacity in sorted(self.capacities.items())
        ]

    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._dispatch_once()
                self._last_error = None
            except Exception as exc:  # 调度器不得因一次存储争用而退出
                self._last_error = type(exc).__name__
            self._wake_event.wait(self.poll_interval_seconds)
            self._wake_event.clear()

    def _dispatch_once(self) -> None:
        executor = self._executor
        if executor is None:
            return
        for queued_job in self.journal.fetch_queued_jobs():
            if self._stop_event.is_set():
                return
            resource_group = queued_job["resource_group"]
            semaphore = self._semaphores.get(resource_group)
            if semaphore is None:
                self.journal.mark_failed(
                    queued_job["id"],
                    {
                        "code": "UNKNOWN_RESOURCE_GROUP",
                        "message": f"未知资源组: {resource_group}",
                    },
                )
                continue
            if not semaphore.acquire(blocking=False):
                continue
            claimed = self.journal.claim_job(queued_job["id"])
            if claimed is None:
                semaphore.release()
                continue
            try:
                executor.submit(self._run_job, claimed, semaphore)
            except Exception:
                semaphore.release()
                raise

    def _run_job(
        self,
        job: dict[str, Any],
        semaphore: threading.BoundedSemaphore,
    ) -> None:
        try:
            with self._registry_lock:
                adapter = self._registry.get(job["kind"])
            if adapter is None:
                self.journal.mark_failed(
                    job["id"],
                    {
                        "code": "ADAPTER_NOT_FOUND",
                        "message": f"未注册适配器: {job['kind']}",
                    },
                )
                return

            context = ExecutionContext(job["id"], self.journal, self.artifacts)
            context.ensure_not_cancelled()
            result = adapter.execute(context, job["input"])
            context.ensure_not_cancelled()
            self.journal.mark_succeeded(job["id"], result)
        except CancelledExecution:
            self.journal.mark_cancelled(job["id"])
        except AdapterExecutionError as exc:
            self.journal.mark_failed(
                job["id"],
                {
                    "code": "ADAPTER_EXECUTION_FAILED",
                    "message": str(exc),
                    "retryable": False,
                    "details": {},
                },
            )
        except AdapterRuntimeError as exc:
            self.journal.mark_failed(
                job["id"],
                {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "details": exc.details,
                },
            )
        except Exception as exc:
            self.journal.mark_failed(
                job["id"],
                {
                    "code": "ADAPTER_INTERNAL_ERROR",
                    "message": "适配器执行发生内部错误",
                    "type": type(exc).__name__,
                    "retryable": False,
                    "details": {},
                },
            )
        finally:
            # GPU/CPU 资源槽必须覆盖成功、失败、超时和用户取消的所有出口。
            # 真实 CLI 监督器会先终止进程树，随后才回到这里释放槽位，避免
            # “任务已取消但 CUDA 子进程仍占显存”时调度下一个重模型。
            semaphore.release()
            self.wake()
