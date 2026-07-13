from __future__ import annotations

import time
from typing import Iterable


def create_job(client, auth_headers, key: str, kind: str, input_data=None, metadata=None):
    headers = {**auth_headers, "Idempotency-Key": key}
    controls = dict(input_data or {})
    prompt = str(controls.pop("prompt", ""))
    mock_controls = {
        field: controls.pop(field)
        for field in ("delay_ms", "fail")
        if field in controls
    }
    metadata = metadata or {}
    return client.post(
        "/v1/jobs",
        headers=headers,
        json={
            "capability": kind,
            "model_id": "mock/default",
            "profile": "contract-test",
            "prompt": prompt,
            "negative_prompt": None,
            "input_artifacts": metadata.get("input_artifacts", []),
            "output_spec": {"mock": mock_controls, **controls},
            "seed": metadata.get("seed", 7),
            "workflow_version": metadata.get("workflow_version", "contract-v1"),
            "project_id": metadata.get("project_id", "project-1"),
            "stage_type": metadata.get("stage_type", kind),
            "work_item_id": metadata.get("work_item_id", key),
        },
    )


def wait_for_status(
    client,
    auth_headers,
    job_id: str,
    statuses: str | Iterable[str],
    timeout: float = 4,
):
    expected = {statuses} if isinstance(statuses, str) else set(statuses)
    deadline = time.monotonic() + timeout
    last_job = None
    while time.monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200, response.text
        last_job = response.json()
        if last_job["status"] in expected:
            return last_job
        time.sleep(0.01)
    raise AssertionError(f"任务未进入 {expected}，最后状态: {last_job}")
