"""Live HTTP acceptance against ONLY the isolated student_ui_server on port 8198.

Start that fixture server first. It uses temporary data and mock model responses.
No real model or search credentials are used by this acceptance flow.
"""
import json
import os
import time
import uuid

import httpx


def main():
    port = int(os.getenv("CASE_SIM_TEST_PORT", "8198"))
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        # Authenticate the known disposable fixture before creating any test data.
        teacher = request("POST", "/api/teacher/login", json={"password": "student-ui-test"})["teacher_token"]
        credentials = {"username": "smoke-" + uuid.uuid4().hex[:12], "password": "offline-smoke-pass"}
        identity = request("POST", "/api/student/register", json={**credentials, "real_name": "联调测试学生"})
        client.headers["Authorization"] = "Bearer " + identity["access_token"]
        student_id = identity["student"]["id"]
        cases = request("GET", "/api/student/cases")
        assert cases and cases[0]["student_token"] == "benchmark-demo-v4"
        url = "/api/play/" + cases[0]["student_token"]
        state = request("POST", url + "/sessions", json={"student_name": "联调测试学生"})
        sid = state["session_id"]
        for node in state["play"]["nodes"]:
            request("POST", url + "/decide", json={"student_name": "联调测试学生", "session_id": sid,
                "node_id": node["id"], "option_key": node["options"][0]["key"],
                "input_text": "考虑投入、市场份额与现金流", "duration_ms": 100})
        restored = request("GET", url + f"/sessions/{sid}")
        assert restored["finished"] and len(restored["turns"]) == 3
        for _ in range(30):
            review = request("GET", url + "/review", params={"session_id": sid})
            if review["status"] == "succeeded":
                break
            assert review["status"] in {"running", "pending"}, review["status"]
            time.sleep(0.2)
        assert review["status"] == "succeeded" and len(review["dimensions"]) == 6
        assert review['dimensions'][0]['name'] == '促销（Promotion）'
        assert review['dimensions'][-1]['name'] == '风险提示'
        chat = client.post(url + "/chat", json={"session_id": sid, "message": "如何理解市场份额？"})
        chat.raise_for_status()
        assert "【案例资料】" in chat.text and "read_case" not in chat.text and '"done": true' in chat.text
        messages = request("GET", url + f"/sessions/{sid}/messages")
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert 'read_case' not in messages[-1]['content']
        students = request("GET", "/api/teacher/students", params={"token": teacher})
        assert any(r["id"] == student_id and r["real_name"] == "联调测试学生" for r in students)
        records = request("GET", f"/api/cases/{cases[0]['id']}/records", params={"token": teacher})
        record = next(r for r in records if r["session_id"] == sid)
        assert record["student_id"] == student_id and len(record["turns"]) == 3 and record["review"]
        request("PATCH", f"/api/reviews/{record['review_id']}", params={"token": teacher}, json={"conclusion": "教师联调校准结论"})
        assert request("GET", url + "/review", params={"session_id": sid})["conclusion"] == "教师联调校准结论"
        request("POST", "/api/student/logout")
        login = request("POST", "/api/student/login", json=credentials)
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        assert request("GET", "/api/student/records")[0]["session_id"] == sid
        html = client.get("/student/login")
        assert html.status_code == 200 and "/assets/" in html.text
        print(json.dumps({"result": "passed", "transport": "live HTTP", "database": "temporary",
            "ai": "mock", "steps": ["register", "case_list", "three_decisions", "restore",
            "review", "tutor", "teacher_students", "teacher_records", "teacher_calibration",
            "relogin_saved_records", "spa_asset_entry"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
