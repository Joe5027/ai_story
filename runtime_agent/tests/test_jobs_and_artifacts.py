from __future__ import annotations

import hashlib

import pytest

from .helpers import create_job, wait_for_status


@pytest.mark.parametrize(
    "kind",
    ["llm", "text2image", "image_edit", "image2video", "motion_render"],
)
def test_every_mock_adapter_succeeds_and_creates_verifiable_artifact(
    client, auth_headers, kind
):
    created = create_job(
        client,
        auth_headers,
        f"adapter-{kind}",
        kind,
        {"prompt": f"run {kind}", "delay_ms": 1},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    job = wait_for_status(client, auth_headers, job_id, "succeeded")
    assert job["result"]["capability"] == kind
    assert job["result"]["artifact_ids"]

    listing = client.get(f"/v1/jobs/{job_id}/artifacts", headers=auth_headers)
    assert listing.status_code == 200
    artifact = listing.json()["artifacts"][0]
    assert artifact["sha256"] == job["result"]["sha256"]
    assert artifact["download_url"] == f"/v1/artifacts/{artifact['id']}"

    unauthorized = client.get(artifact["download_url"])
    assert unauthorized.status_code == 401
    download = client.get(artifact["download_url"], headers=auth_headers)
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == artifact["sha256"]
    assert download.headers["x-artifact-sha256"] == artifact["sha256"]


def test_idempotency_replays_same_request_and_rejects_conflict(client, auth_headers):
    first = create_job(client, auth_headers, "same-key", "llm", {"prompt": "same"})
    assert first.status_code == 202
    assert first.headers["idempotent-replay"] == "false"
    assert "job" not in first.json()
    assert first.json()["id"] == first.json()["job_id"]
    current = client.get(f"/v1/jobs/{first.json()['job_id']}", headers=auth_headers)
    assert current.status_code == 200
    assert "job" not in current.json()
    assert {"status", "result", "artifacts", "error"} <= set(current.json())

    replay = create_job(client, auth_headers, "same-key", "llm", {"prompt": "same"})
    assert replay.status_code == 200
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["job_id"] == first.json()["job_id"]

    conflict = create_job(client, auth_headers, "same-key", "llm", {"prompt": "different"})
    assert conflict.status_code == 409
    error = conflict.json()["error"]
    assert error["code"] == "IDEMPOTENCY_CONFLICT"
    assert error["details"]["existing_request_hash"] != error["details"]["request_hash"]


def test_failed_job_and_filtered_query(client, auth_headers):
    response = create_job(
        client,
        auth_headers,
        "expected-failure",
        "image_edit",
        {"fail": True, "delay_ms": 1},
    )
    job = wait_for_status(client, auth_headers, response.json()["job_id"], "failed")
    assert job["error"]["code"] == "ADAPTER_EXECUTION_FAILED"

    listing = client.get(
        "/v1/jobs?status=failed&capability=image_edit",
        headers=auth_headers,
    )
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert listing.json()["jobs"][0]["id"] == job["id"]


def test_non_mock_model_fails_with_runtime_not_ready(client, auth_headers):
    response = client.post(
        "/v1/jobs",
        headers={**auth_headers, "Idempotency-Key": "real-runtime-not-ready"},
        json={
            "capability": "llm",
            "model_id": "ollama/qwen3:8b",
            "profile": "draft",
            "prompt": "hello",
            "negative_prompt": "",
            "input_artifacts": [],
            "output_spec": {},
            "seed": 1,
            "workflow_version": "v1",
            "project_id": "project-1",
            "stage_type": "rewrite",
            "work_item_id": "item-1",
        },
    )
    assert response.status_code == 202
    job = wait_for_status(client, auth_headers, response.json()["job_id"], "failed")
    assert job["error"]["code"] == "RUNTIME_NOT_READY"
    assert job["error"]["retryable"] is True


def test_queued_and_running_jobs_can_be_cancelled(client, auth_headers):
    running_response = create_job(
        client,
        auth_headers,
        "gpu-running",
        "text2image",
        {"delay_ms": 400},
    )
    running_id = running_response.json()["job_id"]
    wait_for_status(client, auth_headers, running_id, "running")

    queued_response = create_job(
        client,
        auth_headers,
        "gpu-queued",
        "image2video",
        {"delay_ms": 1},
    )
    queued_id = queued_response.json()["job_id"]
    queued = wait_for_status(client, auth_headers, queued_id, "queued")
    unauthorized_delete = client.delete(f"/v1/jobs/{queued_id}")
    assert unauthorized_delete.status_code == 401
    cancelled_queued = client.delete(
        f"/v1/jobs/{queued_id}",
        headers=auth_headers,
    )
    assert cancelled_queued.status_code == 202
    assert cancelled_queued.json()["status"] == "cancelled"

    cancel_running = client.delete(
        f"/v1/jobs/{running_id}",
        headers=auth_headers,
    )
    assert cancel_running.status_code == 202
    assert cancel_running.json()["cancel_requested"] is True
    wait_for_status(client, auth_headers, running_id, "cancelled")


def test_download_detects_tampered_artifact(client, auth_headers, app):
    response = create_job(
        client,
        auth_headers,
        "tamper-artifact",
        "motion_render",
        {"delay_ms": 1},
    )
    job_id = response.json()["job_id"]
    wait_for_status(client, auth_headers, job_id, "succeeded")
    artifact = client.get(
        f"/v1/jobs/{job_id}/artifacts", headers=auth_headers
    ).json()["artifacts"][0]
    metadata = client.get(
        f"/v1/artifacts/{artifact['id']}/metadata", headers=auth_headers
    )
    assert metadata.status_code == 200
    assert metadata.json()["artifact"]["sha256"] == artifact["sha256"]
    registered = app.state.journal.get_artifact(artifact["id"])
    path = app.state.artifacts.root / registered["relative_path"]
    path.write_bytes(b"tampered")

    download = client.get(artifact["download_url"], headers=auth_headers)
    assert download.status_code == 409
    assert download.json()["error"]["code"] == "ARTIFACT_INTEGRITY_ERROR"
