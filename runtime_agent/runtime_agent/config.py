"""运行时代理的环境配置。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值")


def _read_toml(path: Path) -> dict[str, Any]:
    """读取固定配置文件；显式指定但不存在或格式错误时必须停止启动。"""

    if not path.is_file():
        raise ValueError(f"RUNTIME_AGENT_CONFIG_PATH 指向的文件不存在: {path}")
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Runtime Agent TOML 无法读取: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Runtime Agent TOML 顶层必须是对象")
    return payload


def _path_from_config(value: str, *, config_path: Path | None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and config_path is not None:
        path = config_path.parent / path
    return path.resolve()


def _toml_value(section: dict[str, Any], name: str, default: Any) -> Any:
    value = section.get(name, default)
    if isinstance(value, (dict, list)):
        raise ValueError(f"[agent].{name} 类型无效")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """集中保存可测试、可通过环境变量覆盖的运行参数。"""

    db_path: Path
    artifacts_dir: Path
    bearer_token: str
    allow_unauthenticated_loopback: bool = True
    require_auth_non_loopback: bool = True
    gpu_capacity: int = 1
    cpu_motion_capacity: int = 2
    poll_interval_seconds: float = 0.02
    service_name: str = "ai-story-runtime-agent"
    service_version: str = "0.1.0"
    config_path: Path | None = None

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        """从环境变量构造配置，路径默认落在本服务目录的 var 下。"""

        package_root = Path(__file__).resolve().parent.parent
        var_root = package_root / "var"
        raw_config_path = os.getenv("RUNTIME_AGENT_CONFIG_PATH", "").strip()
        config_path = Path(raw_config_path).expanduser().resolve() if raw_config_path else None
        document = _read_toml(config_path) if config_path else {}
        agent = document.get("agent", {})
        if not isinstance(agent, dict):
            raise ValueError("Runtime Agent TOML 的 [agent] 必须是对象")

        def env_or_toml(env_name: str, toml_name: str, default: Any) -> Any:
            raw_env = os.getenv(env_name)
            if raw_env is not None:
                return raw_env
            return _toml_value(agent, toml_name, default)

        raw_poll_ms = env_or_toml(
            "RUNTIME_AGENT_POLL_INTERVAL_MS", "poll_interval_ms", 20
        )
        if isinstance(raw_poll_ms, bool) or (
            os.getenv("RUNTIME_AGENT_POLL_INTERVAL_MS") is None
            and not isinstance(raw_poll_ms, int)
        ):
            raise ValueError("RUNTIME_AGENT_POLL_INTERVAL_MS 必须是正整数")
        poll_ms = int(raw_poll_ms)
        if poll_ms < 1:
            raise ValueError("RUNTIME_AGENT_POLL_INTERVAL_MS 必须大于 0")
        token = str(
            env_or_toml(
                "RUNTIME_AGENT_BEARER_TOKEN",
                "bearer_token",
                "change-me-in-production",
            )
        ).strip()
        if not token:
            raise ValueError("RUNTIME_AGENT_BEARER_TOKEN 不能为空")

        db_path = _path_from_config(
            str(
                env_or_toml(
                    "RUNTIME_AGENT_DB_PATH",
                    "db_path",
                    str(var_root / "runtime-agent.sqlite3"),
                )
            ),
            config_path=config_path,
        )
        artifacts_dir = _path_from_config(
            str(
                env_or_toml(
                    "RUNTIME_AGENT_ARTIFACTS_DIR",
                    "artifacts_dir",
                    str(var_root / "artifacts"),
                )
            ),
            config_path=config_path,
        )

        def bool_setting(env_name: str, toml_name: str, default: bool) -> bool:
            raw_env = os.getenv(env_name)
            if raw_env is not None:
                return _read_bool(env_name, default)
            value = _toml_value(agent, toml_name, default)
            if not isinstance(value, bool):
                raise ValueError(f"[agent].{toml_name} 必须是布尔值")
            return value

        def positive_int_setting(env_name: str, toml_name: str, default: int) -> int:
            raw_env = os.getenv(env_name)
            raw_value = raw_env if raw_env is not None else _toml_value(
                agent, toml_name, default
            )
            if isinstance(raw_value, bool) or (
                raw_env is None and not isinstance(raw_value, int)
            ):
                raise ValueError(f"{env_name} 必须是正整数")
            value = int(raw_value)
            if value < 1:
                raise ValueError(f"{env_name} 必须大于 0")
            return value

        return cls(
            db_path=db_path,
            artifacts_dir=artifacts_dir,
            bearer_token=token,
            allow_unauthenticated_loopback=bool_setting(
                "RUNTIME_AGENT_ALLOW_UNAUTHENTICATED_LOOPBACK",
                "allow_unauthenticated_loopback",
                True,
            ),
            require_auth_non_loopback=bool_setting(
                "RUNTIME_AGENT_REQUIRE_AUTH_NON_LOOPBACK",
                "require_auth_non_loopback",
                True,
            ),
            gpu_capacity=positive_int_setting(
                "RUNTIME_AGENT_GPU_CAPACITY", "gpu_capacity", 1
            ),
            cpu_motion_capacity=positive_int_setting(
                "RUNTIME_AGENT_CPU_MOTION_CAPACITY", "cpu_motion_capacity", 2
            ),
            poll_interval_seconds=poll_ms / 1000,
            config_path=config_path,
        )

    @property
    def resource_capacities(self) -> dict[str, int]:
        return {
            "gpu": self.gpu_capacity,
            "cpu_motion": self.cpu_motion_capacity,
        }
