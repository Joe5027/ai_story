from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runtime_agent.config import RuntimeSettings
from runtime_agent.main import create_app


@pytest.fixture
def runtime_settings(tmp_path):
    return RuntimeSettings(
        db_path=tmp_path / "journal.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        bearer_token="contract-test-token",
        allow_unauthenticated_loopback=False,
        require_auth_non_loopback=True,
        gpu_capacity=1,
        cpu_motion_capacity=2,
        poll_interval_seconds=0.005,
    )


@pytest.fixture
def app(runtime_settings):
    return create_app(runtime_settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(runtime_settings):
    return {"Authorization": f"Bearer {runtime_settings.bearer_token}"}
