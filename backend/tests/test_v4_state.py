import asyncio
import io, zipfile
from pathlib import Path

from backend.app.domain import decision_engine
from backend.app.routers import teacher
from backend.app.schemas import LoginRequest


def test_delta_uses_snapshot_difference():
    before = {"revenue": 10, "gross_margin": "60.0%", "market_share": "20.0%", "cash_flow": "稳健"}
    after = {"revenue": 12.5, "gross_margin": "62.5%", "market_share": "18.0%", "cash_flow": "收紧"}
    got = decision_engine.delta(before, after)
    assert got["revenue"] == 2.5
    assert got["gross_margin"] == 2.5
    assert got["market_share"] == -2.0
    assert got["cash_flow"] == {"from": "稳健", "to": "收紧"}


def test_decision_domain_has_no_model_or_fabricated_fallback():
    assert not hasattr(decision_engine, "decide")
    assert not hasattr(decision_engine, "chat_json")
    assert not hasattr(decision_engine, "fallback")


def test_migration_module_is_idempotent():
    from backend.app.migrations import migrate
    migrate()

def test_teacher_login_uses_configured_password(tmp_path, monkeypatch):
    """验证教师登录令牌持久化到数据库。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.db import Base, get_db
    from backend.app.main import app
    from fastapi.testclient import TestClient
    from backend.app.models import TeacherSession
    
    # 创建临时数据库
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    
    def override_get_db():
        with sessionmaker(bind=engine)() as db:
            yield db
    
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setenv("TEACHER_PASSWORD", "secret")
    
    try:
        client = TestClient(app)
        response = client.post("/api/teacher/login", json={"password": "secret"})
        assert response.status_code == 200
        token = response.json()["teacher_token"]
        
        # 验证令牌已持久化到数据库
        with sessionmaker(bind=engine)() as db:
            row = db.get(TeacherSession, token)
            assert row is not None
            assert row.expires_at is not None
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

def test_docx_text_extraction_reads_paragraphs():
    xml = '''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>背景文字</w:t></w:r></w:p></w:body></w:document>'''.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z: z.writestr("word/document.xml", xml)
    raw = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert "背景文字" in z.read("word/document.xml").decode()

def test_publish_gate_requires_three_nodes_and_review_dimensions():
    from backend.app.domain import rules
    from backend.app import schemas
    assert len(rules.FRAMEWORK_DIMENSIONS[rules.CASE_TYPE_TO_FRAMEWORK[schemas.CaseType.marketing.value]]) == 5

def test_case_version_is_model_field_for_snapshot_freeze():
    from backend.app.models import Case
    from backend.app.models import Session
    assert hasattr(Case, "version") and hasattr(Session, "case_snapshot_json")


def test_teacher_record_accepts_v4_result_json():
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from backend.app import models
    from backend.app.routers.teacher import list_records

    metrics = {"revenue": 15, "gross_margin": "65.0%", "market_share": "10.0%", "cash_flow": "稳健"}
    delta = {"revenue": 0, "gross_margin": 0, "market_share": 0, "cash_flow": {"from": "稳健", "to": "稳健"}}
    turn = SimpleNamespace(
        id=1, node_id=2, node=SimpleNamespace(idx=1), chosen_option="A", input_text=None,
        result_json={"before_metrics": metrics, "after_metrics": metrics, "delta_metrics": delta, "summary": "结果", "source": "preset"},
        before_metrics_json=metrics, after_metrics_json=metrics, delta_metrics_json=delta,
        result_source="preset", duration_ms=100, created_at=datetime.now(), attempt_no=1,
    )
    session = SimpleNamespace(student_name="演示学生", student_id=None, attempt_no=1, turns=[turn])
    scalars = SimpleNamespace(all=lambda: [session])

    # db mock 需要区分 TeacherSession 查询和 Case 查询
    teacher_session_row = SimpleNamespace(expires_at=datetime.now() + timedelta(hours=12))
    case_row = SimpleNamespace(id=16)
    def mock_get(model, key):
        if model is models.TeacherSession:
            return teacher_session_row
        return case_row
    db = SimpleNamespace(get=mock_get, scalars=lambda *_: scalars)

    records = list_records(16, "record-token", db)
    assert records[0].turns[0].result_json.metrics.revenue == 15
    assert records[0].turns[0].after_metrics.revenue == 15
