"""不依赖 FastAPI 的离线核心行为烟测。"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime_agent.adapters import build_mock_registry
from runtime_agent.artifacts import ArtifactStore
from runtime_agent.errors import AgentError
from runtime_agent.journal import JobJournal
from runtime_agent.scheduler import RuntimeScheduler


def wait(journal: JobJournal, job_id: str, expected: str, timeout: float = 4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = journal.get_job(job_id)
        if job["status"] == expected:
            return job
        time.sleep(0.01)
    raise AssertionError(f"{job_id} 未进入 {expected}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="runtime-agent-core-") as raw_root:
        root = Path(raw_root)
        journal = JobJournal(root / "journal.sqlite3")
        journal.initialize()
        artifacts = ArtifactStore(root / "artifacts", journal)
        artifacts.initialize()
        registry = build_mock_registry()
        scheduler = RuntimeScheduler(
            journal=journal,
            artifacts=artifacts,
            registry=registry,
            capacities={"gpu": 1, "cpu_motion": 2},
            poll_interval_seconds=0.005,
        )
        scheduler.start()
        try:
            job, replay = journal.create_or_get_job(
                idempotency_key="core-smoke-1",
                request_hash="hash-1",
                kind="text2image",
                input_data={"model_id": "mock/default", "prompt": "smoke", "delay_ms": 10},
                metadata={"workflow_version": "smoke-v1"},
                resource_group="gpu",
                runtime_generation=1,
            )
            assert replay is False
            replayed, replay = journal.create_or_get_job(
                idempotency_key="core-smoke-1",
                request_hash="hash-1",
                kind="text2image",
                input_data={"model_id": "mock/default", "prompt": "smoke", "delay_ms": 10},
                metadata={"workflow_version": "smoke-v1"},
                resource_group="gpu",
                runtime_generation=1,
            )
            assert replay is True and replayed["id"] == job["id"]
            try:
                journal.create_or_get_job(
                    idempotency_key="core-smoke-1",
                    request_hash="different",
                    kind="text2image",
                    input_data={},
                    metadata={},
                    resource_group="gpu",
                    runtime_generation=1,
                )
            except AgentError as exc:
                assert exc.code == "IDEMPOTENCY_CONFLICT"
            else:
                raise AssertionError("幂等冲突未被拒绝")

            scheduler.wake()
            succeeded = wait(journal, job["id"], "succeeded")
            artifact = journal.list_artifacts(job["id"])[0]
            verified = artifacts.verified_path(artifact)
            assert hashlib.sha256(verified.read_bytes()).hexdigest() == artifact["sha256"]
            assert succeeded["result"]["capability"] == "text2image"

            cancellable, _ = journal.create_or_get_job(
                idempotency_key="core-smoke-cancel",
                request_hash="hash-cancel",
                kind="motion_render",
                input_data={"model_id": "mock/default", "delay_ms": 300},
                metadata={},
                resource_group="cpu_motion",
                runtime_generation=1,
            )
            scheduler.wake()
            wait(journal, cancellable["id"], "running")
            journal.request_cancel(cancellable["id"])
            wait(journal, cancellable["id"], "cancelled")
        finally:
            scheduler.stop()

        interrupted, _ = journal.create_or_get_job(
            idempotency_key="core-smoke-recovery",
            request_hash="hash-recovery",
            kind="llm",
            input_data={"model_id": "mock/default", "prompt": "recover"},
            metadata={},
            resource_group="cpu_motion",
            runtime_generation=1,
        )
        journal.claim_job(interrupted["id"])
        assert journal.recover_interrupted_jobs() == {"requeued": 1, "cancelled": 0}
        assert journal.get_job(interrupted["id"])["status"] == "queued"

    print("runtime-agent core smoke: PASS")


if __name__ == "__main__":
    main()
