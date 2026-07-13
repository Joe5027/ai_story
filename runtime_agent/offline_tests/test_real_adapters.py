from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime_agent.adapters import MockAdapter
from runtime_agent.errors import RuntimeNotReady
from runtime_agent.real_adapters import (
    ComfyUIAdapter,
    ComfyUIConfig,
    FFmpegMotionAdapter,
    FFmpegMotionConfig,
    LightX2VAdapter,
    LightX2VConfig,
    OllamaAdapter,
    OllamaConfig,
)


class FakeContext:
    def __init__(self, root: Path):
        self.job_id = "job-1"
        self.artifacts = SimpleNamespace(root=root)
        self.writes = []

    def ensure_not_cancelled(self):
        return None

    def sleep(self, milliseconds):
        return None

    def write_artifact(self, name, content, media_type="application/json", metadata=None):
        item = {
            "id": f"artifact-{len(self.writes) + 1}",
            "name": name,
            "content": content,
            "media_type": media_type,
            "metadata": metadata or {},
        }
        self.writes.append(item)
        return item


class FakeOllamaTransport:
    def __init__(self):
        self.calls = []

    def json(self, method, url, *, payload=None, timeout):
        self.calls.append((method, url, payload, timeout))
        return {"choices": [{"message": {"content": "本地回答"}}]}

    def bytes(self, url, *, timeout):
        raise AssertionError("Ollama 不应下载二进制")


def _manifest():
    return {
        "manifest_version": 1,
        "workflow_version": "wf-v1",
        "workflow": {
            "1": {"inputs": {"text": ""}},
            "2": {"inputs": {"text": ""}},
            "3": {"inputs": {"width": 0, "height": 0}},
            "4": {"inputs": {"seed": 0}},
            "5": {"inputs": {"image": ""}},
        },
        "bindings": {
            "prompt": {"node_id": "1", "input_name": "text"},
            "negative_prompt": {"node_id": "2", "input_name": "text"},
            "width": {"node_id": "3", "input_name": "width"},
            "height": {"node_id": "3", "input_name": "height"},
            "seed": {"node_id": "4", "input_name": "seed"},
            "input_artifacts": [{"node_id": "5", "input_name": "image"}],
        },
        "outputs": [{"node_id": "9", "collection": "images"}],
    }


class FakeComfyTransport:
    def __init__(self):
        self.submitted = None

    def json(self, method, url, *, payload=None, timeout):
        if url.endswith("/prompt"):
            self.submitted = payload
            return {"prompt_id": "prompt-1"}
        if "/history/prompt-1" in url:
            return {
                "prompt-1": {
                    "outputs": {
                        "9": {
                            "images": [
                                {"filename": "result.png", "subfolder": "", "type": "output"}
                            ]
                        }
                    }
                }
            }
        raise AssertionError(url)

    def bytes(self, url, *, timeout):
        assert "/view?" in url
        return b"png-bytes", "image/png"


def test_ollama_posts_openai_compatible_chat_contract(tmp_path):
    transport = FakeOllamaTransport()
    adapter = OllamaAdapter(OllamaConfig(base_url="http://127.0.0.1:11434"), transport)
    context = FakeContext(tmp_path)
    result = adapter.execute(
        context,
        {
            "model_id": "ollama/qwen3:8b",
            "prompt": "你好",
            "negative_prompt": "简洁回答",
            "seed": 9,
        },
    )
    method, url, payload, _ = transport.calls[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
    assert payload["model"] == "qwen3:8b"
    assert payload["messages"][-1] == {"role": "user", "content": "你好"}
    assert payload["seed"] == 9
    assert result["text"] == "本地回答"
    assert context.writes[0]["media_type"] == "text/plain"


def test_comfyui_manifest_explicitly_binds_all_inputs_and_executes_contract(tmp_path):
    manifest_path = tmp_path / "workflow.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    transport = FakeComfyTransport()
    adapter = ComfyUIAdapter(
        "text2image",
        ComfyUIConfig(manifest_path=manifest_path, workflow_version="wf-v1"),
        transport,
    )
    input_data = {
        "prompt": "城市夜景",
        "negative_prompt": "模糊",
        "seed": 42,
        "input_artifacts": [{"filename": "reference.png"}],
        "output_spec": {"width": 768, "height": 512},
    }
    workflow, _ = adapter.bind_workflow(input_data)
    assert workflow["1"]["inputs"]["text"] == "城市夜景"
    assert workflow["2"]["inputs"]["text"] == "模糊"
    assert workflow["3"]["inputs"] == {"width": 768, "height": 512}
    assert workflow["4"]["inputs"]["seed"] == 42
    assert workflow["5"]["inputs"]["image"] == "reference.png"

    context = FakeContext(tmp_path)
    result = adapter.execute(context, input_data)
    assert transport.submitted["prompt"]["1"]["inputs"]["text"] == "城市夜景"
    assert result["artifact_ids"] == ["artifact-1"]
    assert context.writes[0]["content"] == b"png-bytes"


def test_comfyui_rejects_manifest_version_drift(tmp_path):
    manifest = _manifest()
    manifest["workflow_version"] = "wrong"
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    adapter = ComfyUIAdapter(
        "image_edit",
        ComfyUIConfig(manifest_path=path, workflow_version="expected"),
    )
    with pytest.raises(RuntimeNotReady) as error:
        adapter.load_manifest()
    assert error.value.code == "RUNTIME_NOT_READY"


def test_lightx2v_uses_fixed_argv_and_shell_false(tmp_path):
    model_path = tmp_path / "model"
    model_path.mkdir()
    input_path = tmp_path / "input.png"
    input_path.write_bytes(b"image")
    captured = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter = LightX2VAdapter(
        LightX2VConfig(
            command_template=(
                "python",
                "-m",
                "lightx2v.infer",
                "--model",
                "{model_path}",
                "--input",
                "{input_path}",
                "--prompt",
                "{prompt}",
                "--output",
                "{output_path}",
            ),
            model_path=model_path,
        ),
        fake_runner,
    )
    context = FakeContext(tmp_path)
    result = adapter.execute(
        context,
        {"prompt": "gentle motion", "input_artifacts": [str(input_path)]},
    )
    assert captured["shell"] is False
    assert isinstance(captured["command"], list)
    assert result["artifact_ids"] == ["artifact-1"]


def test_lightx2v_rejects_uncontrolled_template_placeholder(tmp_path):
    adapter = LightX2VAdapter(
        LightX2VConfig(
            command_template=("python", "{prompt.__class__}"),
            model_path=tmp_path,
        )
    )
    with pytest.raises(RuntimeNotReady):
        adapter.build_command({"prompt": "x"}, tmp_path / "out.mp4")


@pytest.mark.parametrize("mode", ["zoom", "pan", "crossfade"])
def test_ffmpeg_motion_plan_has_short_segments_and_eight_frame_overlap(mode):
    adapter = FFmpegMotionAdapter()
    plan = adapter.build_plan(
        {
            "output_spec": {
                "motion": mode,
                "duration": 12,
                "fps": 24,
                "width": 1280,
                "height": 720,
            }
        }
    )
    assert plan["mode"] == mode
    assert all(item["duration_seconds"] <= 5 for item in plan["segments"])
    assert all(item["overlap_frames"] == 8 for item in plan["segments"][1:])


def test_ffmpeg_compose_uses_xfade_exact_trim_and_artifact_metadata(tmp_path):
    first = tmp_path / "segment-1.mp4"
    second = tmp_path / "segment-2.mp4"
    first.write_bytes(b"segment-1")
    second.write_bytes(b"segment-2")
    captured = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        Path(command[-1]).write_bytes(b"composed-video")
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter = FFmpegMotionAdapter(
        FFmpegMotionConfig(executable=str(Path(sys.executable))),
        fake_runner,
    )
    context = FakeContext(tmp_path)
    result = adapter.execute(
        context,
        {
            "input_artifacts": [
                {"path": str(first), "duration_seconds": 4.5},
                {"path": str(second), "duration_seconds": 4.0},
            ],
            "output_spec": {
                "motion": "compose",
                "duration": 8.0,
                "fps": 24,
                "width": 1280,
                "height": 720,
                "trim_exact_duration": True,
            },
        },
    )

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "xfade=transition=fade" in filter_graph
    assert command[command.index("-t") + 1] == "8.0"
    assert "-loop" not in command
    assert captured["shell"] is False
    assert result["metadata"]["plan"]["natural_duration_seconds"] > 8.0
    assert context.writes[0]["metadata"] == {
        "duration_seconds": 8.0,
        "fps": 24,
        "width": 1280,
        "height": 720,
        "mode": "compose",
    }


def test_mock_refuses_non_mock_model_instead_of_silent_fallback(tmp_path):
    adapter = MockAdapter("llm", "cpu_motion", "mock")
    with pytest.raises(RuntimeNotReady) as error:
        adapter.execute(FakeContext(tmp_path), {"model_id": "ollama/qwen3:8b"})
    assert error.value.code == "RUNTIME_NOT_READY"
