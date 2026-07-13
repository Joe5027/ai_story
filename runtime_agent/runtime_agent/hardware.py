"""无需额外依赖的只读硬件快照。"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _memory_total_bytes() -> int | None:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except (AttributeError, OSError, ValueError):
        return None


def _nvidia_gpus() -> list[dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        completed = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    for index, line in enumerate(completed.stdout.splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            total_mib = int(parts[1])
            free_mib = int(parts[2])
        except ValueError:
            continue
        result.append(
            {
                "index": index,
                "name": parts[0],
                "memory_total_mib": total_mib,
                "memory_free_mib": free_mib,
                "driver_version": parts[3],
            }
        )
    return result


def hardware_snapshot(runtime_root: Path) -> dict[str, Any]:
    """返回不含用户名、主机名和环境变量的最小硬件快照。"""

    disk = shutil.disk_usage(Path(runtime_root).resolve())
    memory = _memory_total_bytes()
    return {
        "platform": platform.system().lower(),
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": memory,
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "gpus": _nvidia_gpus(),
    }
