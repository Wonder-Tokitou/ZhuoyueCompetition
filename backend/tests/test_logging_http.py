"""Dedicated-process HTTP log tests: temp DB/artifacts, no network/model calls."""
import asyncio
import logging
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch


class HttpLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {
            "DATABASE_URL": "sqlite:///" + (folder / "test.db").as_posix(),
            "ARTIFACT_ROOT": str(folder / "artifacts"),
            "CASE_SIM_LOG_FILE": str(folder / "test.log"),
            "TEACHER_PASSWORD": "test-password-only",
            "LLM_API_KEY": "", "DEEPSEEK_API_KEY": "", "LOG_AI_CONTENT": "0",
        })
        cls.env.start()
        from backend.app import db
        from backend.app.main import app
        from backend.app.observability import configure_logging
        configure_logging()
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.app.routers import student
        from backend.app.integrations.ai import task_runner
        from backend.app.integrations.ai import artifacts
        # Explicitly bind fixtures even when another test imported the app first.
        cls.engine = create_engine("sqlite:///" + (folder / "test.db").as_posix(), connect_args={"check_same_thread": False})
        db.Base.metadata.create_all(cls.engine)
        cls.factory = sessionmaker(bind=cls.engine, autoflush=False)
        cls.patches = ExitStack()
        for module in (db, student, task_runner):
            cls.patches.enter_context(patch.object(module, "SessionLocal", cls.factory))
        cls.patches.enter_context(patch.object(artifacts, "ARTIFACT_ROOT", folder / "artifacts"))
        cls.previous_overrides = app.dependency_overrides.copy()
        def override_db():
            with cls.factory() as session:
                yield session
        app.dependency_overrides[db.get_db] = override_db
        from backend.app.seed import seed_demo_case
        from fastapi.testclient import TestClient
        with db.SessionLocal() as session:
            seed_demo_case(session)
        cls.client = TestClient(app)
        from backend.tests.account_helpers import login_student
        login_student(cls.client, cls.factory, real_name="private-student-name")
        cls.log_file = folder / "test.log"

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        from backend.app.main import app
        app.dependency_overrides.clear()
        app.dependency_overrides.update(cls.previous_overrides)
        cls.patches.close()
        cls.engine.dispose()
        # A dedicated process also creates the application's initial engine on import.
        # Dispose it only when it belongs to this fixture, not another test's database.
        from backend.app.db import engine as initial_engine
        if initial_engine.url.database and Path(initial_engine.url.database).resolve() == (Path(cls.temp.name) / "test.db").resolve():
            initial_engine.dispose()
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_case_sim", False):
                handler.close()
                root.removeHandler(handler)
        cls.env.stop()
        cls.temp.cleanup()

    def test_teacher_student_upload_errors_and_stream(self):
        from backend.app.routers import teacher
        from backend.app.routers import student
        from ai_component.providers.llm import LLMFailed
        token = self.client.post("/api/teacher/login", json={"password": "test-password-only"}).json()["teacher_token"]
        with patch.object(teacher, "schedule_task"):
            result = self.client.post("/api/cases", data={"title": "测试", "case_type": "市场营销类"},
                                      files={"file": ("sample.txt", ("测试教学素材。" * 40).encode(), "text/plain")})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn("x-request-id", result.headers)
        result = self.client.post("/api/cases", data={"title": "测试", "case_type": "市场营销类"},
                                  files={"file": ("empty.txt", b"", "text/plain")})
        self.assertEqual(result.status_code, 400)
        self.client.post("/api/teacher/login", json={"password": {"private-student-input": 1}})
        play = self.client.get("/api/play/benchmark-demo-v4").json()
        result = self.client.post("/api/play/benchmark-demo-v4/decide", json={
            "student_name": "private-student-name", "node_id": play["nodes"][0]["id"],
            "option_key": "A", "duration_ms": 12,
        })
        self.assertEqual(result.status_code, 200, result.text)
        sid = result.json()["session_id"]

        async def answer(*_):
            return "模拟答疑输出"
        with patch('ai_component.agents.tutor_agent.TutorAgent.run', answer):
            stream = self.client.post("/api/play/benchmark-demo-v4/chat",
                                      json={"session_id": sid, "message": "private-question"})
        self.assertIn("模拟答疑输出", stream.text)

        async def broken(*_):
            raise LLMFailed("mock stream HTTP 429")
        with patch('ai_component.agents.tutor_agent.TutorAgent.run', broken):
            stream = self.client.post("/api/play/benchmark-demo-v4/chat",
                                      json={"session_id": sid, "message": "private-question"})
        self.assertIn("429", stream.text)
        self.client.get("/api/cases", params={"token": token})
        edited = self.client.patch("/api/cases/1", params={"token": token}, json={"title": "测试校准"})
        self.assertEqual(edited.status_code, 200)
        self.client.get("/api/play/invalid-private-token")
        text = self.log_file.read_text(encoding="utf-8")
        for expected in ("文件已接收", "素材解析成功", "上传及入库成功", "业务请求失败",
                         "请求参数校验失败", "人工校准已保存", "决策结果已保存", "学生答疑：输出完成", "429"):
            self.assertIn(expected, text)
        for private in (token, "private-student-name", "private-question", "private-student-input",
                        "invalid-private-token", "test-password-only"):
            self.assertNotIn(private, text)

    def test_task_stage_context_and_success(self):
        from backend.app import models
        from backend.app.db import SessionLocal
        from backend.app.integrations.ai import task_runner
        with SessionLocal() as db:
            task_key = "log-test-task-" + os.urandom(4).hex()
            db.add(models.AiTask(task_id=task_key, kind="case_generation", case_id=1, input_json={}))
            db.commit()

        async def fake(db, task):
            task_runner._step(db, task, "mock_generation", detail={"offline": True})
            task.status = "succeeded"
            task.stage = "completed"
            db.commit()
        with patch.object(task_runner, "_run_case_generation", fake):
            asyncio.run(task_runner.run_task(task_key))
        text = self.log_file.read_text(encoding="utf-8")
        for expected in ("task=" + task_key, "AI任务开始", "AI工作流阶段", "mock_generation", "AI任务结束"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
