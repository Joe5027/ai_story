"""可取消的本地 CLI 子进程监督器。"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol, Sequence

from .errors import AdapterRuntimeError, CancelledExecution, RuntimeNotReady


class CancellationContext(Protocol):
    """进程监督器只依赖任务上下文的取消检查能力。"""

    def ensure_not_cancelled(self) -> None: ...


def _read_output_tail(stream: Any, limit_bytes: int) -> str:
    """只读取日志尾部，避免把模型进程的大量输出重新载入内存。"""

    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    start = max(0, size - limit_bytes)
    stream.seek(start)
    content = stream.read()
    prefix = "[output truncated]\n" if start else ""
    return prefix + content.decode("utf-8", errors="replace")


def _wait_for_exit(process: subprocess.Popen[Any], timeout_seconds: float) -> bool:
    try:
        process.wait(timeout=max(0.01, timeout_seconds))
        return True
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return process.poll() is not None


def _windows_taskkill(pid: int, *, force: bool, timeout_seconds: float) -> None:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    try:
        subprocess.run(
            command,
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1.0, timeout_seconds),
        )
    except (OSError, subprocess.SubprocessError):
        # 精简 Windows 环境可能没有 taskkill；下面仍会终止直接子进程。
        return


def _signal_posix_group(process: subprocess.Popen[Any], signal_number: int) -> None:
    try:
        os.killpg(process.pid, signal_number)
    except (ProcessLookupError, PermissionError, OSError):
        return


def terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    grace_seconds: float = 2.0,
) -> None:
    """先终止、后强杀整个进程树，并保证清理异常不会覆盖任务原错误。

    LightX2V 往往会继续派生 Python/CUDA 子进程，仅终止父进程可能留下显存
    占用。Windows 使用 ``taskkill /T``，POSIX 使用独立 session 的进程组；
    宽限期结束后再执行强制清理。该函数是 best-effort，最终仍会对直接子
    进程调用 ``kill``，避免调度槽永久被阻塞。
    """

    if process.poll() is not None:
        return

    grace_seconds = max(0.05, grace_seconds)
    if os.name == "nt":
        _windows_taskkill(process.pid, force=False, timeout_seconds=grace_seconds)
    else:
        _signal_posix_group(process, signal.SIGTERM)

    exited = _wait_for_exit(process, grace_seconds)

    # 即使父进程刚刚退出，也再次清理进程组/树，防止忽略 TERM 的模型子进程
    # 留下 CUDA 上下文。Windows 的首次 /T 已记录整棵父子关系，二次 /F 是
    # 兜底；POSIX 可以在组长退出后继续向同一 PGID 发信号。
    if os.name == "nt":
        _windows_taskkill(process.pid, force=True, timeout_seconds=grace_seconds)
    else:
        _signal_posix_group(process, signal.SIGKILL)

    if not exited and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    _wait_for_exit(process, grace_seconds)


def run_cancellable_process(
    context: CancellationContext,
    command: Sequence[str | os.PathLike[str]],
    *,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
    poll_interval_seconds: float = 0.05,
    terminate_grace_seconds: float = 2.0,
    output_tail_bytes: int = 64 * 1024,
) -> subprocess.CompletedProcess[str]:
    """运行不经过 shell 的 CLI，并把任务取消/超时传播到真实进程树。

    stdout/stderr 写入临时文件而不是 PIPE，避免视频/模型进程输出过多时因
    管道写满而死锁。监督循环每个短周期检查 SQLite journal 的取消标记；
    取消保留 ``CancelledExecution``，超时稳定映射为 ``EXECUTION_TIMEOUT``。
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if not command:
        raise RuntimeNotReady("本地 CLI command 不能为空")

    context.ensure_not_cancelled()
    argv = [os.fspath(item) for item in command]
    popen_kwargs: dict[str, Any] = {
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    with tempfile.TemporaryFile(mode="w+b") as stdout_stream, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_stream:
        popen_kwargs["stdout"] = stdout_stream
        popen_kwargs["stderr"] = stderr_stream
        try:
            process = subprocess.Popen(argv, **popen_kwargs)
        except OSError as exc:
            raise RuntimeNotReady(
                "本地 CLI 进程无法启动",
                {"executable": str(Path(argv[0]).name), "reason": type(exc).__name__},
            ) from exc

        deadline = time.monotonic() + timeout_seconds
        try:
            while process.poll() is None:
                context.ensure_not_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    terminate_process_tree(
                        process,
                        grace_seconds=terminate_grace_seconds,
                    )
                    raise AdapterRuntimeError(
                        "EXECUTION_TIMEOUT",
                        "本地 CLI 任务执行超时",
                        retryable=True,
                        details={"timeout_seconds": timeout_seconds},
                    )
                time.sleep(min(max(0.01, poll_interval_seconds), remaining))
            # 进程与取消请求可能同时完成；写产物前再次以用户取消为准。
            context.ensure_not_cancelled()
        except CancelledExecution:
            terminate_process_tree(
                process,
                grace_seconds=terminate_grace_seconds,
            )
            raise
        except AdapterRuntimeError:
            raise
        except BaseException:
            if process.poll() is None:
                terminate_process_tree(
                    process,
                    grace_seconds=terminate_grace_seconds,
                )
            raise

        stdout = _read_output_tail(stdout_stream, max(1024, output_tail_bytes))
        stderr = _read_output_tail(stderr_stream, max(1024, output_tail_bytes))
        return subprocess.CompletedProcess(argv, int(process.returncode or 0), stdout, stderr)
