"""Offline logging regression suite. Run in a dedicated process with unittest."""
import asyncio
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.observability import SafeFormatter
from backend.app.observability import event
from backend.app.observability import output_summary
from backend.app.observability import request_id
from backend.app.observability import task_id


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(SafeFormatter())
        self.logger = logging.getLogger("case_sim")
        self.old_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.handler)

    def tearDown(self):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.old_level)

    def test_redacts_secrets_and_line_injection_and_keeps_traceback(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "private-api-value"}):
            try:
                raise ValueError("private-api-value token=teacher-value sk-fake-test")
            except ValueError:
                self.logger.exception("provider failed /api/play/student-secret/chat?token=other-secret")
        text = self.stream.getvalue()
        for secret in ("private-api-value", "teacher-value", "fake-test", "student-secret", "other-secret"):
            self.assertNotIn(secret, text)
        self.assertIn("ValueError", text)
        self.assertIn("test_runtime_logging.py", text)
        event("upload", filename="file\nFAKE-SUCCESS.txt")
        self.assertNotIn("\nFAKE-SUCCESS", self.stream.getvalue())

    def test_default_summary_omits_model_text(self):
        with patch.dict(os.environ, {"LOG_AI_CONTENT": "0"}):
            self.assertNotIn("private teaching content", str(output_summary({"reply": "private teaching content"})))
        with patch.dict(os.environ, {"LOG_AI_CONTENT": "1"}):
            self.assertIn("preview", output_summary({"reply": "example"}))

    def test_model_mock_success_invalid_json_retry_and_http_error(self):
        import httpx
        from ai_component.providers import llm
        responses = [httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]}),
                     httpx.Response(200, json={"choices": [{"message": {"content": '{"reply":"ok"}'}}]})]
        transport = httpx.MockTransport(lambda request: responses.pop(0))
        real_client = httpx.AsyncClient
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://example.invalid", "LLM_API_KEY": "private-api-value",
                                     "LLM_MODEL": "mock", "LLM_PROVIDER": "openai_compatible"}):
            with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=transport, **kw)):
                data = asyncio.run(llm.chat_json([{"role": "user", "content": "private-input"}], {"reply": None}, retries=1))
                self.assertEqual(data["reply"], "ok")
            transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"message": "invalid key private-api-value"}}))
            with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=transport, **kw)):
                with self.assertRaises(llm.LLMFailed):
                    asyncio.run(llm.chat_json([], {"reply": None}, retries=0))
            transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("mock timeout")))
            with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=transport, **kw)):
                with self.assertRaises(llm.LLMFailed):
                    asyncio.run(llm.chat_json([], {}, retries=0))
        text = self.stream.getvalue()
        for expected in ("模型调用开始", "模型调用成功", "retry=True", "401", "ReadTimeout"):
            self.assertIn(expected, text)
        self.assertNotIn("private-input", text)
        self.assertNotIn("private-api-value", text)

    def test_viewer_handles_utf8_increment_and_rotation(self):
        from scripts.watch_logs import chunks
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.log"
            path.write_text("教师上传成功\n", encoding="utf-8")
            reader = chunks(path)
            self.assertEqual(next(reader).replace("\r\n", "\n"), "教师上传成功\n")
            with path.open("a", encoding="utf-8") as stream:
                stream.write("学生决策成功\n")
            self.assertEqual(next(reader).replace("\r\n", "\n"), "学生决策成功\n")
            path.rename(path.with_suffix(".log.1"))
            path.write_text("日志轮转成功\n", encoding="utf-8")
            self.assertEqual(next(reader).replace("\r\n", "\n"), "日志轮转成功\n")
            reader.close()

    def test_request_and_task_correlation(self):
        r, t = request_id.set("req-example"), task_id.set("task-example")
        try:
            event("stage", status="running")
        finally:
            request_id.reset(r)
            task_id.reset(t)
        self.assertIn("request=req-example task=task-example", self.stream.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows log console")
    def test_launcher_requests_separate_console_without_starting_server(self):
        from scripts import run_server
        import subprocess
        import sys
        with patch.dict(os.environ, {"APP_HOST": "127.0.0.1", "APP_PORT": "8199"}), \
             patch.object(sys, "argv", ["scripts/run_server.py"]), \
             patch("backend.app.observability.configure_logging"), \
             patch.object(run_server.subprocess, "Popen") as spawn, \
             patch("uvicorn.run") as serve:
            run_server.main()
            args, kwargs = spawn.call_args
            self.assertEqual(args[0][0], sys.executable)
            self.assertEqual(Path(args[0][2]).name, "watch_logs.py")
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NEW_CONSOLE)
            self.assertTrue(Path(args[0][3]).is_absolute())
            self.assertEqual(serve.call_args.kwargs["port"], 8199)
            self.assertFalse(serve.call_args.kwargs["access_log"])


if __name__ == "__main__":
    unittest.main()
