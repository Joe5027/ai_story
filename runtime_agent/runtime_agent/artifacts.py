"""产物落盘、摘要登记和下载完整性校验。"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .errors import AgentError
from .journal import JobJournal


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactStore:
    """只允许在配置根目录内写入和读取已登记产物。"""

    def __init__(self, root: Path, journal: JobJournal) -> None:
        self.root = Path(root).resolve()
        self.journal = journal

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def healthcheck(self) -> bool:
        try:
            self.initialize()
            descriptor, raw_path = tempfile.mkstemp(prefix=".ready-", dir=self.root)
            os.close(descriptor)
            Path(raw_path).unlink(missing_ok=True)
            return True
        except OSError:
            return False

    @staticmethod
    def _safe_filename(name: str) -> str:
        sanitized = _SAFE_NAME.sub("_", Path(name).name).strip("._")
        if not sanitized:
            raise AgentError(400, "INVALID_ARTIFACT_NAME", "产物文件名无效")
        return sanitized[:160]

    def write(
        self,
        *,
        job_id: str,
        name: str,
        content: bytes,
        media_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """原子写入产物并把 SHA256、大小和相对路径登记到 journal。"""

        safe_name = self._safe_filename(name)
        job_dir = (self.root / job_id).resolve()
        if self.root not in job_dir.parents:
            raise AgentError(400, "INVALID_ARTIFACT_PATH", "产物路径无效")
        job_dir.mkdir(parents=True, exist_ok=True)

        target = job_dir / safe_name
        suffix = 0
        while target.exists():
            suffix += 1
            target = job_dir / f"{Path(safe_name).stem}-{suffix}{Path(safe_name).suffix}"

        descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=job_dir)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise

        relative_path = target.relative_to(self.root).as_posix()
        digest = hashlib.sha256(content).hexdigest()
        return self.journal.add_artifact(
            job_id=job_id,
            name=target.name,
            media_type=media_type,
            relative_path=relative_path,
            sha256=digest,
            size_bytes=len(content),
            metadata=metadata,
        )

    def verified_path(self, artifact: dict[str, Any]) -> Path:
        """在返回下载前重新核对路径边界、大小和 SHA256。"""

        path = (self.root / artifact["relative_path"]).resolve()
        if self.root not in path.parents:
            raise AgentError(409, "ARTIFACT_INTEGRITY_ERROR", "产物路径越界")
        if not path.is_file():
            raise AgentError(404, "ARTIFACT_FILE_MISSING", "产物文件不存在")
        if path.stat().st_size != artifact["size_bytes"] or sha256_file(path) != artifact["sha256"]:
            raise AgentError(409, "ARTIFACT_INTEGRITY_ERROR", "产物完整性校验失败")
        return path
