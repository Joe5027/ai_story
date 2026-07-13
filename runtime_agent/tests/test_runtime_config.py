from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime_agent.config import RuntimeSettings
from runtime_agent.errors import RuntimeNotReady
from runtime_agent.main import create_app
from runtime_agent.registry import build_runtime_registry


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _manifest(
    path: Path, workflow_version: str, model_id: str, model_version: str
) -> Path:
    payload = {
        "manifest_version": 1,
        "workflow_version": workflow_version,
        "model_ids": [model_id],
        "model_version": model_version,
        "workflow": {
            "1": {"inputs": {"text": ""}},
            "2": {"inputs": {"text": ""}},
            "3": {"inputs": {"width": 0, "height": 0}},
            "4": {"inputs": {"seed": 0}},
        },
        "bindings": {
            "prompt": {"node_id": "1", "input_name": "text"},
            "negative_prompt": {"node_id": "2", "input_name": "text"},
            "width": {"node_id": "3", "input_name": "width"},
            "height": {"node_id": "3", "input_name": "height"},
            "seed": {"node_id": "4", "input_name": "seed"},
            "input_artifacts": [{"node_id": "1", "input_name": "image"}],
        },
        "outputs": [{"node_id": "9", "collection": "images"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ollama_config(mode: str = "configured") -> str:
    return f'''schema_version = 1
[registry]
mode = "{mode}"
[adapters.ollama]
enabled = true
base_url = "http://127.0.0.1:11434"
model_ids = ["ollama/qwen-fixed"]
model_version = "sha256:qwen-fixed"
workflow_version = "ollama-chat-v1"
timeout_seconds = 120
'''


def test_default_and_mock_mode_never_activate_real_adapter(tmp_path):
    assert all(
        item.capability()["adapter"] == "mock"
        for item in build_runtime_registry(None).values()
    )

    config_path = _write(tmp_path / "runtime.toml", _ollama_config(mode="mock"))
    registry = build_runtime_registry(config_path)
    assert registry["llm"].capability()["adapter"] == "mock"

    configured_without_enabled_adapter = _write(
        tmp_path / "configured-disabled.toml",
        'schema_version = 1\n[registry]\nmode = "configured"\n',
    )
    assert all(
        item.capability()["adapter"] == "mock"
        for item in build_runtime_registry(configured_without_enabled_adapter).values()
    )


def test_configured_mode_activates_only_explicitly_enabled_adapters(tmp_path):
    text_manifest = _manifest(
        tmp_path / "text.json", "text-wf-v1", "comfy/flux-text", "sha256:flux"
    )
    edit_manifest = _manifest(
        tmp_path / "edit.json",
        "edit-wf-v1",
        "comfy/flux-edit",
        "sha256:flux-edit",
    )
    model_path = tmp_path / "wan-model"
    model_path.mkdir()
    executable = Path(sys.executable).as_posix()
    config_path = _write(
        tmp_path / "runtime.toml",
        f'''schema_version = 1
[registry]
mode = "configured"
[adapters.ollama]
enabled = true
base_url = "http://127.0.0.1:11434"
model_ids = ["ollama/qwen-fixed"]
model_version = "sha256:qwen"
workflow_version = "ollama-chat-v1"
[adapters.comfyui_text2image]
enabled = true
base_url = "http://127.0.0.1:8188"
manifest_path = "{text_manifest.name}"
model_ids = ["comfy/flux-text"]
model_version = "sha256:flux"
workflow_version = "text-wf-v1"
[adapters.comfyui_image_edit]
enabled = true
base_url = "http://localhost:8188"
manifest_path = "{edit_manifest.name}"
model_ids = ["comfy/flux-edit"]
model_version = "sha256:flux-edit"
workflow_version = "edit-wf-v1"
[adapters.lightx2v]
enabled = true
model_path = "{model_path.name}"
model_ids = ["lightx2v/wan-fixed"]
model_version = "sha256:wan"
workflow_version = "wan-wf-v1"
command_template = ["{executable}", "-c", "print('offline')"]
[adapters.ffmpeg_motion]
enabled = true
executable = "{executable}"
model_ids = ["ffmpeg/motion-fixed"]
model_version = "ffmpeg-7-fixed"
workflow_version = "motion-wf-v1"
''',
    )

    registry = build_runtime_registry(config_path)
    assert {kind: adapter.capability()["adapter"] for kind, adapter in registry.items()} == {
        "llm": "ollama",
        "text2image": "comfyui",
        "image_edit": "comfyui",
        "image2video": "lightx2v",
        "motion_render": "ffmpeg_motion",
    }
    assert registry["image_edit"].capability()["models"] == [
        {"model_id": "comfy/flux-edit", "version": "sha256:flux-edit", "ready": True}
    ]


def test_enabled_adapter_enforces_model_and_workflow_allowlists(tmp_path):
    registry = build_runtime_registry(
        _write(tmp_path / "runtime.toml", _ollama_config())
    )
    with pytest.raises(RuntimeNotReady) as error:
        registry["llm"].execute(
            object(),
            {
                "model_id": "ollama/unapproved",
                "workflow_version": "ollama-chat-v1",
                "prompt": "不会发出网络请求",
            },
        )
    assert error.value.details == {"model_id": "ollama/unapproved"}

    with pytest.raises(RuntimeNotReady) as error:
        registry["llm"].execute(
            object(),
            {
                "model_id": "ollama/qwen-fixed",
                "workflow_version": "unapproved-workflow",
                "prompt": "不会发出网络请求",
            },
        )
    assert error.value.details["expected"] == "ollama-chat-v1"


def test_runtime_settings_load_toml_and_environment_overrides(monkeypatch, tmp_path):
    config_path = _write(
        tmp_path / "runtime.toml",
        '''schema_version = 1
[registry]
mode = "mock"
[agent]
db_path = "state/journal.sqlite3"
artifacts_dir = "outputs"
gpu_capacity = 3
cpu_motion_capacity = 4
poll_interval_ms = 25
allow_unauthenticated_loopback = false
require_auth_non_loopback = true
''',
    )
    for name in (
        "RUNTIME_AGENT_DB_PATH",
        "RUNTIME_AGENT_ARTIFACTS_DIR",
        "RUNTIME_AGENT_GPU_CAPACITY",
        "RUNTIME_AGENT_CPU_MOTION_CAPACITY",
        "RUNTIME_AGENT_POLL_INTERVAL_MS",
        "RUNTIME_AGENT_ALLOW_UNAUTHENTICATED_LOOPBACK",
        "RUNTIME_AGENT_REQUIRE_AUTH_NON_LOOPBACK",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNTIME_AGENT_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("RUNTIME_AGENT_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RUNTIME_AGENT_GPU_CAPACITY", "1")

    settings = RuntimeSettings.from_env()
    assert settings.config_path == config_path.resolve()
    assert settings.db_path == (tmp_path / "state/journal.sqlite3").resolve()
    assert settings.artifacts_dir == (tmp_path / "outputs").resolve()
    assert settings.gpu_capacity == 1
    assert settings.cpu_motion_capacity == 4
    assert settings.poll_interval_seconds == 0.025
    assert settings.allow_unauthenticated_loopback is False


def test_failed_reload_keeps_current_registry_and_generation(tmp_path):
    config_path = _write(
        tmp_path / "runtime.toml",
        'schema_version = 1\n[registry]\nmode = "mock"\n',
    )
    settings = RuntimeSettings(
        db_path=tmp_path / "journal.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        bearer_token="reload-test-token",
        allow_unauthenticated_loopback=False,
        poll_interval_seconds=0.005,
        config_path=config_path,
    )
    headers = {"Authorization": "Bearer reload-test-token"}

    with TestClient(create_app(settings)) as client:
        before = client.get("/v1/capabilities", headers=headers).json()
        _write(
            config_path,
            '''schema_version = 1
[registry]
mode = "configured"
[adapters.comfyui_text2image]
enabled = true
base_url = "http://127.0.0.1:8188"
manifest_path = "missing-workflow.json"
model_ids = ["comfy/flux-fixed"]
model_version = "sha256:flux"
workflow_version = "wf-v1"
''',
        )
        failed = client.post(
            "/v1/runtime-reloads",
            headers=headers,
            json={"reason": "invalid-config-test"},
        )
        after = client.get("/v1/capabilities", headers=headers).json()
        reloads = client.get("/v1/runtime-reloads", headers=headers).json()

    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "RUNTIME_CONFIG_INVALID"
    assert after["runtime_generation"] == before["runtime_generation"]
    assert all(item["adapter"] == "mock" for item in after["adapters"])
    assert reloads["count"] == 0


def test_successful_reload_uses_the_same_fixed_config_path(tmp_path):
    config_path = _write(
        tmp_path / "runtime.toml",
        'schema_version = 1\n[registry]\nmode = "mock"\n',
    )
    settings = RuntimeSettings(
        db_path=tmp_path / "journal.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        bearer_token="reload-test-token",
        allow_unauthenticated_loopback=False,
        poll_interval_seconds=0.005,
        config_path=config_path,
    )
    headers = {"Authorization": "Bearer reload-test-token"}

    with TestClient(create_app(settings)) as client:
        _write(config_path, _ollama_config())
        response = client.post(
            "/v1/runtime-reloads",
            headers=headers,
            json={"reason": "activate-pinned-ollama"},
        )

    assert response.status_code == 201
    llm = next(
        item for item in response.json()["capabilities"]["adapters"]
        if item["capability"] == "llm"
    )
    assert llm["adapter"] == "ollama"
    assert llm["models"][0]["model_id"] == "ollama/qwen-fixed"
