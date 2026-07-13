from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from runtime_agent.journal import JobJournal
from runtime_agent.main import create_app

from .helpers import create_job, wait_for_status


def _max_concurrency(jobs):
    events = []
    for job in jobs:
        events.append((datetime.fromisoformat(job["started_at"]), 1))
        events.append((datetime.fromisoformat(job["finished_at"]), -1))
    active = 0
    maximum = 0
    for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def test_resource_group_capacities_are_enforced(client, auth_headers):
    gpu_ids = []
    for index, kind in enumerate(("text2image", "image_edit")):
        response = create_job(
            client,
            auth_headers,
            f"gpu-capacity-{index}",
            kind,
            {"delay_ms": 140},
        )
        gpu_ids.append(response.json()["job_id"])

    cpu_ids = []
    for index in range(3):
        response = create_job(
            client,
            auth_headers,
            f"cpu-capacity-{index}",
            "motion_render",
            {"delay_ms": 180},
        )
        cpu_ids.append(response.json()["job_id"])

    gpu_jobs = [wait_for_status(client, auth_headers, job_id, "succeeded") for job_id in gpu_ids]
    cpu_jobs = [wait_for_status(client, auth_headers, job_id, "succeeded") for job_id in cpu_ids]

    assert _max_concurrency(gpu_jobs) == 1
    assert _max_concurrency(cpu_jobs) == 2


def test_sqlite_journal_requeues_running_job_after_restart(runtime_settings, auth_headers):
    journal = JobJournal(runtime_settings.db_path)
    journal.initialize()
    job, replay = journal.create_or_get_job(
        idempotency_key="restart-job",
        request_hash="fixed-hash",
        kind="llm",
        input_data={"model_id": "mock/default", "prompt": "recover", "delay_ms": 1},
        metadata={},
        resource_group="cpu_motion",
        runtime_generation=1,
    )
    assert replay is False
    claimed = journal.claim_job(job["id"])
    assert claimed["status"] == "running"

    restarted_app = create_app(runtime_settings)
    with TestClient(restarted_app) as restarted_client:
        assert restarted_app.state.recovery == {"requeued": 1, "cancelled": 0}
        recovered = wait_for_status(
            restarted_client,
            auth_headers,
            job["id"],
            "succeeded",
        )
    assert recovered["started_at"]
    assert recovered["result"]["capability"] == "llm"
