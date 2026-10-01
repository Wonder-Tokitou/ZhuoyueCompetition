"""Full HTTP flow against run_public --offline-fixture ONLY; no real model calls."""
import argparse
import json
from pathlib import Path
import re
import time
import uuid
import httpx


def check_restart(origin, folder):
    assert folder.resolve().is_relative_to(Path(__file__).resolve().parents[2] / ".test-tmp")
    rows = json.loads((folder / "access-keys.json").read_text(encoding="utf-8"))["spaces"]
    with httpx.Client(base_url=origin, timeout=15, trust_env=False) as client:
        reply = client.post("/access/login", data={"key": rows[0]["key"], "next": "/teacher"})
        token = json.loads(re.search(r'setItem\("teacher_token",("[^"]+")\)', reply.text)[1])
        cases = client.get("/api/cases", params={"token": token}).json()
        assert len(cases) == 1 and cases[0]["status"] == "published"
        login = client.post("/api/student/login", json={"username": "tester", "password": "test-only-strong"})
        login.raise_for_status()
        records = client.get("/api/student/records", headers={"Authorization": "Bearer " + login.json()["access_token"]}).json()
        assert len(records) == 1 and len(records[0]["turns"]) == 3 and records[0]["review"]
    print("PASSED: original key, published case, student account, decisions and review survive full process restart")


def run(origin, folder):
    project = Path(__file__).resolve().parents[2]
    folder = folder.resolve()
    assert folder.is_relative_to(project / ".test-tmp"), "Refuse real test data"
    config = folder / "access-keys.json"
    original = config.read_text(encoding="utf-8")
    rows = json.loads(original)["spaces"]
    clients = []
    try:
        for row in rows[:2]:
            client = httpx.Client(base_url=origin, timeout=45, trust_env=False)
            clients.append(client)
            reply = client.post("/access/login", data={"key": row["key"], "next": "/teacher"})
            token = json.loads(re.search(r'setItem\("teacher_token",("[^"]+")\)', reply.text)[1])
            client.params = {"token": token}
            assert client.get("/api/cases").json() == []
            credentials = {"username": "tester", "password": "test-only-strong", "real_name": row["id"]}
            identity = client.post("/api/student/register", json=credentials)
            identity.raise_for_status()
            client.headers["Authorization"] = "Bearer " + identity.json()["access_token"]
            assert client.get("/api/student/cases").json() == []
        first, other = clients
        material = ("验收素材：校园咖啡企业面对市场竞争，需要讨论渠道、定价及营销投入对现金流与营收的影响。" * 12).encode()
        uploaded = first.post("/api/cases", data={"title": "公网完整链路验收", "case_type": "市场营销类"},
            files={"file": ("acceptance.txt", material, "text/plain")})
        uploaded.raise_for_status()
        created = uploaded.json()
        task_id, cid = created["task_id"], created["case_id"]
        for _ in range(80):
            task = first.get("/api/ai-tasks/" + task_id).json()
            if task["status"] in ("succeeded", "failed"):
                break
            time.sleep(.2)
        assert task["status"] == "succeeded", task
        artifacts = first.get(f"/api/ai-tasks/{task_id}/artifacts").json()
        assert any(r["kind"] == "original_upload" for r in artifacts), artifacts
        assert other.get("/api/ai-tasks/" + task_id).status_code == 404
        assert other.get("/api/cases").json() == []
        publish = first.post(f"/api/cases/{cid}/publish")
        publish.raise_for_status()
        info = publish.json()
        assert first.get(info["qr_code_url"]).headers["content-type"].startswith("image/png")
        assert other.get(info["qr_code_url"]).status_code == 404
        cases = first.get("/api/student/cases").json()
        assert len(cases) == 1
        assert other.get("/api/student/cases").json() == []
        url = "/api/play/" + cases[0]["student_token"]
        state = first.post(url + "/sessions", json={"student_name": "test-1"})
        state.raise_for_status()
        state = state.json()
        assert other.post(url + "/sessions", json={"student_name": "test-2"}).status_code == 404
        sid = state["session_id"]
        for node in state["play"]["nodes"]:
            reply = first.post(url + "/decide", json={"student_name": "test-1", "session_id": sid,
                "node_id": node["id"], "option_key": node["options"][0]["key"], "input_text": "分析现金流", "duration_ms": 100})
            reply.raise_for_status()
        for _ in range(50):
            review = first.get(url + "/review", params={"session_id": sid}).json()
            if review["status"] in ("succeeded", "failed"):
                break
            time.sleep(.2)
        assert review["status"] == "succeeded", review
        chat = first.post(url + "/chat", json={"session_id": sid, "message": "如何理解市场份额？"})
        chat.raise_for_status()
        assert "【案例资料】" in chat.text and '"done": true' in chat.text and "read_case" not in chat.text, chat.text
        assert chat.headers["content-type"].startswith("text/plain")
        records = first.get(f"/api/cases/{cid}/records").json()
        assert records[0]["student_name"] == "test-1" and len(records[0]["turns"]) == 3
        assert first.get("/api/student/records").json()[0]["session_id"] == sid
        config.write_text(json.dumps({"spaces": rows[1:]}), encoding="utf-8")
        assert first.get("/api/cases").status_code == 401
        assert other.get("/api/cases").status_code == 200
        print("PASSED: keyed login, empty spaces, same usernames isolated, upload/artifacts, mock generation, publish/QR, student discovery, 3 decisions, mock review, chunked tutor, teacher records, cross-space denial, live revocation", flush=True)
    finally:
        config.write_text(original, encoding="utf-8")
        for client in clients:
            client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18201")
    parser.add_argument("--root", type=Path, default=Path(".test-tmp/public-flow"))
    parser.add_argument("--restart-check", action="store_true")
    args = parser.parse_args()
    (check_restart if args.restart_check else run)(args.url, args.root)
