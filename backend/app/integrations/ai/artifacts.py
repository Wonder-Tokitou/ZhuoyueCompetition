"""AI 工作流制品存储。

当前实现写入 backend/data/artifacts；业务代码只依赖 ArtifactStore 接口，
后续可以用 OSS 实现替换，而不改变任务和案例逻辑。
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session as OrmSession

from backend.app import models
from backend.app.observability import event


from backend.app.paths import BACKEND_DIR
from .blob_storage import BlobStorage, LocalBlobStorage
ARTIFACT_ROOT = Path(os.getenv("ARTIFACT_ROOT") or (BACKEND_DIR / "data" / "artifacts"))


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:100] or "artifact"


class ArtifactStore:
    """文件制品存储抽象。"""

    def __init__(self, storage: BlobStorage | None = None):
        self.storage = storage

    def _storage(self):
        return self.storage if self.storage is not None else LocalBlobStorage(ARTIFACT_ROOT, BACKEND_DIR)

    def read_json(self, artifact: models.AiArtifact) -> Any:
        data = self._storage().read(artifact.path)
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError("制品校验失败")
        return json.loads(data)

    def delete(self, artifact: models.AiArtifact) -> None:
        """Delete an artifact through the storage seam before removing its index row."""
        self._storage().delete(artifact.path if isinstance(artifact, models.AiArtifact) else artifact)

    def save_bytes(
        self,
        db: OrmSession,
        data: bytes,
        *,
        kind: str,
        task_id: Optional[int] = None,
        case_id: Optional[int] = None,
        session_id: Optional[int] = None,
        suffix: str = ".bin",
        mime: Optional[str] = None,
    ) -> models.AiArtifact:
        folder = Path("cases") / _safe(str(case_id or "unbound"))
        if session_id is not None:
            session = db.get(models.Session, session_id)
            if session is None or session.case_id != case_id:
                raise ValueError("制品与推演案例不匹配")
            # Stable IDs prevent name collisions; the teacher directory displays real names.
            folder = folder / "students" / str(session.student_id or "legacy") / "submissions" / str(session_id)
        if task_id is not None:
            folder = folder / "tasks" / _safe(str(task_id))
        digest = hashlib.sha256(data).hexdigest()
        if not re.fullmatch(r"\.[A-Za-z0-9]+", suffix):
            raise ValueError("无效制品后缀")
        path = folder / ("%s-%s%s" % (_safe(kind), digest[:12], suffix))
        stored_path = self._storage().put(path.as_posix(), data)
        row = models.AiArtifact(
            task_id=task_id,
            case_id=case_id,
            session_id=session_id,
            kind=kind,
            path=stored_path,
            mime=mime or mimetypes.guess_type(str(path))[0] or "application/octet-stream",
            size=len(data),
            sha256=digest,
        )
        db.add(row)
        db.flush()
        event("AI文件产物已写入（事务待提交）", kind=kind, artifact_id=row.id,
              case_id=case_id, session_id=session_id, bytes=len(data), path=stored_path)
        return row

    def save_json(self, db: OrmSession, payload: Any, **kwargs: Any) -> models.AiArtifact:
        data = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        return self.save_bytes(
            db,
            data,
            suffix=".json",
            mime="application/json",
            **kwargs,
        )

    def save_text(self, db: OrmSession, text: str, **kwargs: Any) -> models.AiArtifact:
        return self.save_bytes(
            db,
            text.encode("utf-8"),
            suffix=".txt",
            mime="text/plain; charset=utf-8",
            **kwargs,
        )


artifact_store = ArtifactStore()
