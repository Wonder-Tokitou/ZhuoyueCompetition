from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from backend.app import models
from backend.app.db import Base
from backend.app.db import get_db
from backend.app.main import app
from backend.app.routers import teacher
from backend.app.seed import SEED_STUDENT_TOKEN
from backend.app.seed import seed_demo_case


def temporary_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mvp.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_seed_is_idempotent_and_keeps_eight_tables(tmp_path):
    engine, factory = temporary_db(tmp_path)
    with factory() as db:
        first = seed_demo_case(db).id
        second = seed_demo_case(db).id
        assert first == second
        assert len(db.scalars(select(models.Case)).all()) == 1
    tables = set(inspect(engine).get_table_names())
    assert {
        "framework", "case", "node", "option_result", "session", "turn", "message", "review"
    }.issubset(tables)
    assert {"ai_task", "ai_task_step", "ai_artifact"}.issubset(tables)


def test_login_student_case_and_readable_bad_token(tmp_path, monkeypatch):
    _, factory = temporary_db(tmp_path)
    with factory() as db:
        seed_demo_case(db)
    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setenv("TEACHER_PASSWORD", "mvp-secret")
    client = TestClient(app)
    from backend.tests.account_helpers import login_student
    from backend.tests.teacher_helpers import login_teacher
    login_student(client, factory)
    token = login_teacher(client, monkeypatch)
    play = client.get(f"/api/play/{SEED_STUDENT_TOKEN}")
    assert play.status_code == 200 and len(play.json()["nodes"]) == 3
    bad = client.get("/api/play/not-a-token")
    assert bad.status_code == 404 and bad.json().get("detail")
    app.dependency_overrides.clear()
