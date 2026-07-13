"""从固定 TOML 原子构建 Runtime Agent 适配器注册表。"""

from __future__ import annotations

import shutil
import tomllib
import urllib.parse
from pathlib import Path
from typing import Any

from .adapters import Adapter, build_mock_registry
from .errors import AdapterRuntimeError
from .real_adapters import (
    ComfyUIAdapter,
    ComfyUIConfig,
    FFmpegMotionAdapter,
    FFmpegMotionConfig,
    LightX2VAdapter,
    LightX2VConfig,
    OllamaAdapter,
    OllamaConfig,
)


class RuntimeRegistryConfigError(ValueError):
    """固定配置不安全或不完整；不得替换当前可用 registry。"""


def _load_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeRegistryConfigError(f"配置文件不存在: {path}")
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeRegistryConfigError("Runtime Agent TOML 无法读取") from exc
    if document.get("schema_version") != 1:
        raise RuntimeRegistryConfigError("schema_version 必须固定为 1")
    return document


def _section(document: dict[str, Any], name: str) -> dict[str, Any]:
    adapters = document.get("adapters", {})
    if not isinstance(adapters, dict):
        raise RuntimeRegistryConfigError("[adapters] 必须是对象")
    value = adapters.get(name, {})
    if not isinstance(value, dict):
        raise RuntimeRegistryConfigError(f"[adapters.{name}] 必须是对象")
    return value


def _enabled(section: dict[str, Any], name: str) -> bool:
    value = section.get("enabled", False)
    if not isinstance(value, bool):
        raise RuntimeRegistryConfigError(f"{name}.enabled 必须是布尔值")
    return value


def _required_text(section: dict[str, Any], field: str, section_name: str) -> str:
    value = section.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeRegistryConfigError(f"{section_name}.{field} 必须是非空字符串")
    return value.strip()


def _positive_float(
    section: dict[str, Any], field: str, default: float, section_name: str
) -> float:
    value = section.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeRegistryConfigError(f"{section_name}.{field} 必须大于 0")
    return float(value)


def _positive_int(
    section: dict[str, Any], field: str, default: int, section_name: str
) -> int:
    value = section.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeRegistryConfigError(f"{section_name}.{field} 必须是正整数")
    return value


def _model_ids(section: dict[str, Any], section_name: str) -> tuple[str, ...]:
    values = section.get("model_ids")
    if not isinstance(values, list) or not values:
        raise RuntimeRegistryConfigError(f"{section_name}.model_ids 必须是非空白名单")
    normalized = tuple(str(item).strip() for item in values)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise RuntimeRegistryConfigError(f"{section_name}.model_ids 含空值或重复值")
    return normalized


def _fixed_version(section: dict[str, Any], section_name: str) -> str:
    value = _required_text(section, "model_version", section_name)
    if value.lower().startswith(("replace-", "change-", "todo")):
        raise RuntimeRegistryConfigError(
            f"{section_name}.model_version 仍是占位值，必须替换为固定版本或摘要"
        )
    return value


def _resolved_path(path: Path, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = path.parent / candidate
    return candidate.resolve()


def _local_url(section: dict[str, Any], field: str, section_name: str) -> str:
    value = _required_text(section, field, section_name).rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeRegistryConfigError(f"{section_name}.{field} 不是安全的 HTTP(S) 基地址")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeRegistryConfigError(f"{section_name}.{field} 必须指向本机回环地址")
    return value


def _command_template(section: dict[str, Any], section_name: str) -> tuple[str, ...]:
    value = section.get("command_template")
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(x, str) or not x for x in value)
    ):
        raise RuntimeRegistryConfigError(f"{section_name}.command_template 必须是非空字符串数组")
    return tuple(value)


def _build_ollama(document: dict[str, Any], path: Path) -> OllamaAdapter | None:
    del path
    name = "adapters.ollama"
    section = _section(document, "ollama")
    if not _enabled(section, name):
        return None
    return OllamaAdapter(
        OllamaConfig(
            base_url=_local_url(section, "base_url", name),
            timeout_seconds=_positive_float(section, "timeout_seconds", 120, name),
            allowed_model_ids=_model_ids(section, name),
            model_version=_fixed_version(section, name),
            workflow_version=_required_text(section, "workflow_version", name),
        )
    )


def _build_comfyui(
    document: dict[str, Any], path: Path, capability: str
) -> ComfyUIAdapter | None:
    key = f"comfyui_{capability}"
    name = f"adapters.{key}"
    section = _section(document, key)
    if not _enabled(section, name):
        return None
    adapter = ComfyUIAdapter(
        capability,
        ComfyUIConfig(
            manifest_path=_resolved_path(path, _required_text(section, "manifest_path", name)),
            workflow_version=_required_text(section, "workflow_version", name),
            base_url=_local_url(section, "base_url", name),
            timeout_seconds=_positive_float(section, "timeout_seconds", 180, name),
            poll_interval_seconds=_positive_float(
                section, "poll_interval_seconds", 0.25, name
            ),
            allowed_model_ids=_model_ids(section, name),
            model_version=_fixed_version(section, name),
        ),
    )
    # reload 在替换 registry 之前验证 workflow 结构与固定版本，避免半生效。
    manifest = adapter.load_manifest()
    if manifest.get("model_version") != adapter.config.model_version:
        raise RuntimeRegistryConfigError(
            f"{name} manifest model_version 与固定配置不一致"
        )
    if manifest.get("model_ids") != list(adapter.config.allowed_model_ids):
        raise RuntimeRegistryConfigError(
            f"{name} manifest model_ids 与固定白名单不一致"
        )
    if capability == "image_edit" and not manifest.get("bindings", {}).get(
        "input_artifacts"
    ):
        raise RuntimeRegistryConfigError(
            f"{name} image_edit workflow 必须显式绑定 input_artifacts"
        )
    return adapter


def _build_lightx2v(document: dict[str, Any], path: Path) -> LightX2VAdapter | None:
    name = "adapters.lightx2v"
    section = _section(document, "lightx2v")
    if not _enabled(section, name):
        return None
    model_path = _resolved_path(path, _required_text(section, "model_path", name))
    if not model_path.is_dir():
        raise RuntimeRegistryConfigError(f"{name}.model_path 不存在或不是目录")
    command = _command_template(section, name)
    executable = command[0]
    resolved_executable = _resolved_path(path, executable) if any(
        separator in executable for separator in ("/", "\\")
    ) else None
    if not (shutil.which(executable) or (resolved_executable and resolved_executable.is_file())):
        raise RuntimeRegistryConfigError(f"{name}.command_template 可执行程序不存在")
    if resolved_executable is not None:
        command = (str(resolved_executable), *command[1:])
    return LightX2VAdapter(
        LightX2VConfig(
            command_template=command,
            model_path=model_path,
            timeout_seconds=_positive_float(section, "timeout_seconds", 900, name),
            allowed_model_ids=_model_ids(section, name),
            model_version=_fixed_version(section, name),
            workflow_version=_required_text(section, "workflow_version", name),
        )
    )


def _build_ffmpeg(document: dict[str, Any], path: Path) -> FFmpegMotionAdapter | None:
    name = "adapters.ffmpeg_motion"
    section = _section(document, "ffmpeg_motion")
    if not _enabled(section, name):
        return None
    executable = _required_text(section, "executable", name)
    resolved_executable = _resolved_path(path, executable) if any(
        separator in executable for separator in ("/", "\\")
    ) else None
    if not (shutil.which(executable) or (resolved_executable and resolved_executable.is_file())):
        raise RuntimeRegistryConfigError(f"{name}.executable 不存在")
    return FFmpegMotionAdapter(
        FFmpegMotionConfig(
            executable=str(resolved_executable) if resolved_executable else executable,
            timeout_seconds=_positive_float(section, "timeout_seconds", 300, name),
            max_segment_seconds=_positive_float(
                section, "max_segment_seconds", 5, name
            ),
            overlap_frames=_positive_int(section, "overlap_frames", 8, name),
            allowed_model_ids=_model_ids(section, name),
            model_version=_fixed_version(section, name),
            workflow_version=_required_text(section, "workflow_version", name),
        )
    )


def build_runtime_registry(config_path: Path | None) -> dict[str, Adapter]:
    """完整构建后再返回；调用方只在成功时原子替换当前 registry。"""

    registry: dict[str, Adapter] = dict(build_mock_registry())
    if config_path is None:
        return registry
    document = _load_document(config_path)
    registry_section = document.get("registry", {})
    if not isinstance(registry_section, dict):
        raise RuntimeRegistryConfigError("[registry] 必须是对象")
    mode = registry_section.get("mode", "mock")
    if mode == "mock":
        return registry
    if mode != "configured":
        raise RuntimeRegistryConfigError("[registry].mode 只能是 mock 或 configured")

    try:
        builders = (
            _build_ollama(document, config_path),
            _build_comfyui(document, config_path, "text2image"),
            _build_comfyui(document, config_path, "image_edit"),
            _build_lightx2v(document, config_path),
            _build_ffmpeg(document, config_path),
        )
    except RuntimeRegistryConfigError:
        raise
    except (AdapterRuntimeError, ValueError) as exc:
        raise RuntimeRegistryConfigError(str(exc)) from exc
    for adapter in builders:
        if adapter is not None:
            registry[adapter.kind] = adapter
    return registry
