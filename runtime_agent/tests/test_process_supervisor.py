from __future__ import annotations

import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest

from runtime_agent.artifacts import ArtifactStore
from runtime_agent.errors import AdapterRuntimeError, CancelledExecution
from runtime_agent.journal import JobJournal
from runtime_agent.process_supervisor import run_cancellable_process
from runtime_agent.scheduler import RuntimeScheduler


class EventCancellationContext:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def ensure_not_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise CancelledExecution("test cancellation")


def test_real_process_is_terminated_promptly_when_job_is_cancelled():
    context = EventCancellationContext()
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    started_at = time.monotonic()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_cancellable_process,
            context,
            command,
            timeout_seconds=30,
            poll_interval_seconds=0.02,
            terminate_grace_seconds=0.1,
        )
        time.sleep(0.1)
        context.cancelled.set()
        with pytest.raises(CancelledExecution):
            future.result(timeout=4)

    assert time.monotonic() - started_at < 4


def test_real_process_timeout_is_stable_and_releases_process():
    context = EventCancellationContext()
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    started_at = time.monotonic()

    with pytest.raises(AdapterRuntimeError) as error:
        run_cancellable_process(
            context,
            command,
            timeout_seconds=0.1,
            poll_interval_seconds=0.02,
            terminate_grace_seconds=0.1,
        )

    assert error.value.code == "EXECUTION_TIMEOUT"
    assert error.value.retryable is True
    assert time.monotonic() - started_at < 4


@dataclass
class SupervisedProcessThenSuccessAdapter:
    kind: str = "llm"
    resource_group: str = "gpu"

    def capability(self):
        return {"capability": self.kind, "resource_group": self.resource_group}

    def execute(self, context, input_data):
        failure = input_data.get("failure")
        if failure in {"cancelled", "timeout"}:
            run_cancellable_process(
                context,
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout_seconds=0.1 if failure == "timeout" else 30,
                poll_interval_seconds=0.02,
                terminate_grace_seconds=0.1,
            )
        return {"artifact_ids": [], "metadata": {"slot_reused": True}}


def _wait_for_terminal(journal: JobJournal, job_id: str, timeout: float = 3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = journal.get_job(job_id)
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {journal.get_job(job_id)}")


def _wait_for_status(journal: JobJournal, job_id: str, expected: str, timeout: float = 3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = journal.get_job(job_id)
        if job["status"] == expected:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {expected}: {journal.get_job(job_id)}")


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [("cancelled", "cancelled"), ("timeout", "failed")],
)
def test_gpu_slot_is_released_after_cancel_or_timeout(
    tmp_path,
    failure,
    expected_status,
):
    journal = JobJournal(tmp_path / "journal.sqlite3")
    journal.initialize()
    artifacts = ArtifactStore(tmp_path / "artifacts", journal)
    artifacts.initialize()
    scheduler = RuntimeScheduler(
        journal=journal,
        artifacts=artifacts,
        registry={"llm": SupervisedProcessThenSuccessAdapter()},
        capacities={"gpu": 1},
        poll_interval_seconds=0.005,
    )

    failed_job, _ = journal.create_or_get_job(
        idempotency_key=f"first-{failure}",
        request_hash=f"first-{failure}",
        kind="llm",
        input_data={"failure": failure},
        metadata={},
        resource_group="gpu",
        runtime_generation=1,
    )
    next_job, _ = journal.create_or_get_job(
        idempotency_key=f"next-{failure}",
        request_hash=f"next-{failure}",
        kind="llm",
        input_data={},
        metadata={},
        resource_group="gpu",
        runtime_generation=1,
    )

    scheduler.start()
    try:
        _wait_for_status(journal, failed_job["id"], "running")
        if failure == "cancelled":
            journal.request_cancel(failed_job["id"])
        first_result = _wait_for_terminal(journal, failed_job["id"])
        next_result = _wait_for_terminal(journal, next_job["id"])
    finally:
        scheduler.stop()

    assert first_result["status"] == expected_status
    if failure == "timeout":
        assert first_result["error"]["code"] == "EXECUTION_TIMEOUT"
    assert next_result["status"] == "succeeded"
    assert scheduler.resource_snapshot() == [
        {"name": "gpu", "capacity": 1, "in_use": 0, "available": 1}
    ]
