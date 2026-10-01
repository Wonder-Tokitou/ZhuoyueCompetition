"""Gateway boundary tests: no developer database, live model or network."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from scripts.public_gateway import create_gateway, COOKIE
from scripts.run_public import provision


class PublicGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "keys.json"
        self.rows = provision(self.path)
        self.calls = []
        self.workers = {ident: {"url": "http://" + ident, "password": "private"} for ident in self.rows}
        def upstream(req):
            self.calls.append(req)
            if req.url.path == "/api/teacher/login":
                return httpx.Response(200, json={"teacher_token": "teacher-" + req.url.host})
            if req.url.path.endswith("/chat"):
                return httpx.Response(200, text='data: {"delta":"回答"}\n\ndata: {"done":true}\n\n', headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"space": req.url.host, "scheme": req.headers.get("x-forwarded-proto"), "host": req.headers["host"]})
        transport = httpx.MockTransport(upstream)
        real = httpx.AsyncClient
        with patch("scripts.public_gateway.httpx.AsyncClient", side_effect=lambda **kw: real(transport=transport, **kw)):
            self.app = create_gateway(self.path, self.workers, "https://test.example")
        self.client = TestClient(self.app, base_url="https://test.example")
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def login(self, ident="test-1", next_path="/teacher"):
        return self.client.post("/access/login", data={"key": self.rows[ident]["key"], "next": next_path})

    def test_unauthenticated_all_business_resources_blocked(self):
        for path in ("/teacher", "/student/foo", "/media/qrcode/secret.png", "/assets/main.js"):
            self.assertIn("进入测试空间", self.client.get(path).text)
        self.assertEqual(self.client.get("/api/cases").status_code, 401)
        self.assertEqual(self.calls, [])

    def test_five_persistent_keys_and_same_space_both_roles(self):
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(provision(self.path), self.rows)
        for ident in self.rows:
            response = self.login(ident)
            self.assertIn("HttpOnly", response.headers["set-cookie"])
            self.assertIn("Secure", response.headers["set-cookie"])
            self.assertNotIn(self.rows[ident]["key"], response.text)
            for path in ("/api/cases", "/api/student/cases"):
                self.assertEqual(self.client.get(path).json()["space"], ident)

    def test_remove_or_rotate_key_revokes_active_cookie(self):
        self.login()
        remaining = [r for ident, r in self.rows.items() if ident != "test-1"]
        self.path.write_text(json.dumps({"spaces": remaining}), encoding="utf-8")
        self.assertEqual(self.client.get("/api/cases").status_code, 401)
        self.assertIn("密钥无效", self.login().text)

    def test_invalid_config_fails_closed(self):
        self.login()
        self.path.write_text("{", encoding="utf-8")
        self.assertEqual(self.client.get("/api/cases").status_code, 503)

    def test_origin_and_forwarding(self):
        self.assertEqual(self.client.post("/access/login", headers={"origin": "https://evil.example"}).status_code, 403)
        self.login()
        result = self.client.get("/api/cases", headers={"x-forwarded-proto": "http", "x-forwarded-host": "evil.example"}).json()
        self.assertEqual(result["scheme"], "https")
        self.assertEqual(result["host"], "test.example")
        self.assertNotIn("cookie", self.calls[-1].headers)

    def test_logout_and_legacy_login(self):
        self.login()
        self.assertEqual(self.client.post("/api/teacher/login", json={"password": "local-dev-only"}).status_code, 403)
        self.client.get("/access/logout")
        self.assertNotIn(COOKIE, self.client.cookies)
        self.assertEqual(self.client.get("/api/cases").status_code, 401)

    def test_chat_fetch_framing_survives_tunnel(self):
        self.login()
        response = self.client.post("/api/play/example/chat", json={"message": "问题"})
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn('"done":true', response.text)
        self.assertIn('回答', response.text)

    def test_no_external_next_and_rate_limit(self):
        self.assertIn('location.replace("/student")', self.login(next_path="https://evil.example").text)
        for _ in range(15):
            response = self.client.post("/access/login", data={"key": "invalid"})
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
