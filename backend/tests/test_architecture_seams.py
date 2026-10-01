"""Import guards and host-adapter regression checks for the agreed architecture."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

from sqlalchemy import select
from backend.app import models
from backend.app.domain import rules
from backend.app.integrations.ai import task_runner, workflow, artifacts
from backend.tests.test_student_workflow import env
from backend.tests.teacher_helpers import login_teacher

ROOT = Path(__file__).resolve().parents[2]


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def test_ai_and_contracts_never_import_host_frameworks():
    for folder in ("ai_component", "contracts"):
        for path in (ROOT / folder).rglob("*.py"):
            if "tests" in path.parts:
                continue
            for name in imports(path):
                assert name.split(".")[0] not in {"backend", "sqlalchemy", "fastapi"}, (path, name)
    for path in (ROOT / "backend/app/domain").glob("*.py"):
        assert not any(name.startswith("ai_component") for name in imports(path)), path
    for path in (ROOT / "backend/app/routers").glob("*.py"):
        assert not any(name.startswith("ai_component") for name in imports(path)), path
    for path in (ROOT / "backend/app/integrations/ai").glob("*.py"):
        assert not any(".routers" in name for name in imports(path)), path


def test_ai_can_import_without_loading_platform_or_database():
    script = "import ai_component,sys; assert not any(n.startswith(('backend','sqlalchemy','fastapi')) for n in sys.modules)"
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True)


def test_stable_data_and_rule_paths_from_another_cwd(tmp_path, monkeypatch):
    from backend.app.paths import BACKEND_DIR, PROJECT_ROOT
    monkeypatch.chdir(tmp_path)
    assert PROJECT_ROOT == ROOT
    assert BACKEND_DIR == ROOT / "backend"
    assert rules.RULES_PATH == ROOT / "docs/商科规则.json"
    assert artifacts.BACKEND_DIR == ROOT / "backend"


def test_teacher_edit_during_generation_is_not_overwritten(env, monkeypatch):
    _, factory = env
    with factory() as db:
        case = db.get(models.Case, 1)
        case.status = "generating"
        original_version = case.version
        db.add(models.AiTask(task_id="teacher-race", case_id=1, kind="case_generation",
                            status="queued", input_json={"case_version": original_version, "previous_status": "published"}))
        db.commit()
    async def generate(*_):
        with factory() as other:
            case = other.get(models.Case, 1)
            case.background = "教师刚保存的内容"
            case.version += 1
            other.commit()
        return {"background": "AI旧候选", "nodes": [], "base_metrics": {}}, []
    monkeypatch.setattr(workflow, "generate_valid_baseline", generate)
    asyncio.run(task_runner.run_task("teacher-race"))
    with factory() as db:
        task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == "teacher-race"))
        assert task.status == "failed" and "未覆盖" in task.error
        assert db.get(models.Case, 1).background == "教师刚保存的内容"
        assert db.scalar(select(models.AiArtifact).where(models.AiArtifact.kind == "generated_case"))


def test_generation_submission_remains_idempotent(env, monkeypatch):
    client, factory = env
    from backend.app.routers import teacher
    monkeypatch.setattr(teacher, "schedule_task", lambda _: None)
    token = login_teacher(client, monkeypatch)
    a = client.post("/api/cases/1/ai-tasks", params={"token": token}, json={"idempotency_key": "same-input"})
    b = client.post("/api/cases/1/ai-tasks", params={"token": token}, json={"idempotency_key": "same-input"})
    assert a.status_code == b.status_code == 200
    assert a.json()["task_id"] == b.json()["task_id"]
    with factory() as db:
        assert len(db.scalars(select(models.AiTask)).all()) == 1


def test_teacher_fix_adapter_preserves_authority(env, monkeypatch):
    from ai_component.agents import teacher_advice
    client, factory = env
    model = AsyncMock(return_value={"reply": "建议分阶段投入"})
    monkeypatch.setattr(teacher_advice, "chat_json", model)
    with factory() as db:
        case = db.get(models.Case, 1)
        before = case.background
        node_id = case.nodes[0].id
    token = login_teacher(client, monkeypatch)
    response = client.post("/api/cases/1/ai-fix", params={"token": token},
                           json={"message": "如何优化", "node_id": node_id})
    assert response.status_code == 200 and response.json()["reply"] == "建议分阶段投入"
    assert "【节点序号】" in model.call_args.args[0][1]["content"]
    with factory() as db:
        assert db.get(models.Case, 1).background == before


def test_storage_adapter_swap_preserves_metadata_and_trace(env):
    import pytest
    _, factory = env
    class MemoryStorage:
        def __init__(self):
            self.rows = {}
        def put(self, key, data):
            locator = "memory://" + key
            self.rows[locator] = data
            return locator
        def read(self, locator):
            return self.rows[locator]
    memory = MemoryStorage()
    store = artifacts.ArtifactStore(memory)
    with factory() as db:
        row = store.save_json(db, {"dimensions": [1, 2, 3]}, kind="adapter-check", case_id=1)
        db.commit()
        assert row.path.startswith("memory://cases/1/")
        assert store.read_json(row) == {"dimensions": [1, 2, 3]}
        memory.rows[row.path] = b"tampered"
        with pytest.raises(ValueError, match="校验失败"):
            store.read_json(row)


def test_existing_relative_local_artifact_locator_still_reads(tmp_path):
    import hashlib
    from types import SimpleNamespace
    from backend.app.integrations.ai.blob_storage import LocalBlobStorage
    base = tmp_path / "backend"
    root = base / "data/artifacts"
    target = root / "old.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"old":true}')
    store = artifacts.ArtifactStore(LocalBlobStorage(root, base))
    row = SimpleNamespace(path="data/artifacts/old.json", sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    assert store.read_json(row) == {"old": True}


def test_tutor_search_filters_numeric_identifiers_before_network(env, monkeypatch):
    from backend.app.integrations.ai import tutor
    from backend.tests.test_student_workflow import begin
    client, factory = env
    state = begin(client)
    search = AsyncMock(side_effect=AssertionError("private query must not leave host"))
    monkeypatch.setattr(tutor, "search_business", search)
    with factory() as db:
        tools = tutor.SessionTutorTools(db, db.get(models.Session, state["session_id"]))
        rows, notice = asyncio.run(tools.search("查询学生编号123456"))
        assert not rows and notice
    search.assert_not_called()
