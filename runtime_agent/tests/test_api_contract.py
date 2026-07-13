from __future__ import annotations

from fastapi.testclient import TestClient

from runtime_agent.auth import is_loopback_host
from runtime_agent.config import RuntimeSettings
from runtime_agent.main import create_app


def test_live_is_public_and_other_routes_require_bearer(client, auth_headers):
    live = client.get("/v1/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "live"

    unauthorized = client.get("/v1/health/ready")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"
    assert unauthorized.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert unauthorized.json()["request_id"]

    ready = client.get("/v1/health/ready", headers=auth_headers)
    assert ready.status_code == 200
    ready_payload = ready.json()
    assert ready_payload["status"] == "ready"
    assert ready_payload["checks"] == {
        "journal": True,
        "artifacts": True,
        "scheduler": True,
        "runtime_registry": True,
    }
    assert ready_payload["queue"]["status_counts"] == {}
    assert {item["name"]: item["available"] for item in ready_payload["resource_groups"]} == {
        "cpu_motion": 2,
        "gpu": 1,
    }


def test_request_id_and_stable_not_found_envelope(client, auth_headers):
    response = client.get(
        "/v1/does-not-exist",
        headers={**auth_headers, "X-Request-ID": "contract-request-1"},
    )
    assert response.status_code == 404
    assert response.headers["x-request-id"] == "contract-request-1"
    assert response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "请求的资源不存在",
            "retryable": False,
            "details": None,
        },
        "request_id": "contract-request-1",
    }


def test_validation_errors_use_the_same_envelope(client, auth_headers):
    response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={"capability": "not-a-capability", "model_id": "mock/default"},
    )
    assert response.status_code == 422
    body = response.json()
    payload = body["error"]
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["message"] == "请求参数校验失败"
    assert isinstance(payload["details"], list)
    assert payload["retryable"] is False
    assert body["request_id"]


def test_capabilities_list_all_mock_adapters_and_resource_capacities(client, auth_headers):
    response = client.get("/v1/capabilities", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert {item["capability"] for item in payload["adapters"]} == {
        "llm",
        "text2image",
        "image_edit",
        "image2video",
        "motion_render",
    }
    assert {item["name"]: item["capacity"] for item in payload["resource_groups"]} == {
        "cpu_motion": 2,
        "gpu": 1,
    }
    assert payload["service_version"] == "0.1.0"
    assert "disk_free_bytes" in payload["hardware"]
    image2video = next(
        item for item in payload["adapters"] if item["capability"] == "image2video"
    )
    assert image2video["native_max_duration_seconds"] == 5
    assert image2video["models"][0]["ready"] is True


def test_runtime_reload_increments_and_persists_generation(client, auth_headers):
    before = client.get("/v1/capabilities", headers=auth_headers).json()
    response = client.post(
        "/v1/runtime-reloads",
        headers=auth_headers,
        json={"reason": "contract-test"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["reload"]["status"] == "succeeded"
    assert payload["reload"]["generation"] == before["runtime_generation"] + 1
    assert payload["capabilities"]["runtime_generation"] == payload["reload"]["generation"]

    listing = client.get("/v1/runtime-reloads", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert listing.json()["reloads"][0]["reason"] == "contract-test"


def test_nonloopback_auth_requirement_can_be_disabled(tmp_path):
    settings = RuntimeSettings(
        db_path=tmp_path / "journal.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        bearer_token="token",
        allow_unauthenticated_loopback=False,
        require_auth_non_loopback=False,
        poll_interval_seconds=0.005,
    )
    with TestClient(create_app(settings)) as local_client:
        response = local_client.get("/v1/capabilities")
    assert response.status_code == 200


def test_loopback_host_detection_is_explicit():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("::ffff:127.0.0.1")
    assert not is_loopback_host("testclient")
    assert not is_loopback_host("203.0.113.20")
