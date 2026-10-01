"""Student-owned submission lifecycle operations."""
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.integrations.ai.artifacts import artifact_store
from backend.app.integrations.ai.tutor import save_case_file


def _remove_artifacts(db: Session, session_id: int) -> list[str]:
    task_ids = select(models.AiTask.id).where(models.AiTask.session_id == session_id)
    rows = db.scalars(select(models.AiArtifact).where(
        (models.AiArtifact.session_id == session_id) | models.AiArtifact.task_id.in_(task_ids)
    )).all()
    # Remove indexes in the transaction; physical cleanup only happens after commit.
    locators = [row.path for row in rows]
    for row in rows:
        db.delete(row)
    return locators


def _delete_files(db: Session, locators: list[str]) -> None:
    remaining = set(db.scalars(select(models.AiArtifact.path)).all())
    for locator in set(locators) - remaining:
        try:
            artifact_store.delete(locator)
        except FileNotFoundError:
            pass


def overwrite_submission(db: Session, session: models.Session, snapshot: dict, base_metrics: dict, case_version: int) -> models.Session:
    """Reset a run in-place so teachers continue to see one authoritative record."""
    artifacts = _remove_artifacts(db, session.id)
    for task in list(session.ai_tasks):
        db.delete(task)
    for row in list(session.reviews):
        db.delete(row)
    for row in list(session.messages):
        db.delete(row)
    for row in list(session.turns):
        db.delete(row)
    session.created_at = datetime.now()
    db.flush()
    session.attempt_no = 1
    session.case_version = case_version
    session.case_snapshot_json = snapshot
    session.current_metrics_json = base_metrics
    session.finished_at = None
    db.flush()
    save_case_file(db, session)
    db.commit()
    _delete_files(db, artifacts)
    db.refresh(session)
    return session


def delete_submission(db: Session, session: models.Session) -> None:
    artifacts = _remove_artifacts(db, session.id)
    for task in list(session.ai_tasks):
        db.delete(task)
    db.delete(session)
    db.commit()
    _delete_files(db, artifacts)


def rollback_submission(db: Session, session: models.Session, node_idx: int) -> models.Session:
    """Remove the chosen node and every later turn so students can revise the path."""
    if node_idx not in (1, 2, 3):
        raise ValueError("node_idx 必须是 1、2 或 3")
    artifacts = _remove_artifacts(db, session.id)
    for task in list(session.ai_tasks):
        db.delete(task)
    for row in list(session.reviews):
        db.delete(row)
    for row in list(session.messages):
        db.delete(row)
    for turn in list(session.turns):
        if turn.attempt_no == session.attempt_no and turn.node is not None and turn.node.idx >= node_idx:
            db.delete(turn)
    remaining = sorted([t for t in session.turns if t.attempt_no == session.attempt_no and t.node is not None and t.node.idx < node_idx], key=lambda t: t.node.idx)
    session.finished_at = None
    session.current_metrics_json = remaining[-1].after_metrics_json if remaining else session.case_snapshot_json.get("base_metrics", {})
    db.flush()
    save_case_file(db, session)
    db.commit()
    _delete_files(db, artifacts)
    db.refresh(session)
    return session
