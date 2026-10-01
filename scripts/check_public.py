"""Read-only external acceptance: login each key and check initial empty libraries.

Does not create cases/accounts or call AI. Never prints keys or access tokens.
"""
import argparse
import json
from pathlib import Path
import re

import httpx


def check(origin, config, require_empty=False):
    rows = json.loads(config.read_text(encoding="utf-8"))["spaces"]
    with httpx.Client(base_url=origin, timeout=30, trust_env=False) as anonymous:
        assert anonymous.get("/api/cases").status_code == 401
        assert "进入测试空间" in anonymous.get("/teacher/login").text
        assert "进入测试空间" in anonymous.get("/student").text
    for row in rows:
        with httpx.Client(base_url=origin, timeout=30, trust_env=False) as client:
            login = client.post("/access/login", data={"key": row["key"], "next": "/teacher"})
            login.raise_for_status()
            match = re.search(r'setItem\("teacher_token",("[^"]+")\)', login.text)
            assert match, "Key login failed"
            token = json.loads(match[1])
            response = client.get("/api/cases", params={"token": token})
            response.raise_for_status()
            cases = response.json()
            assert isinstance(cases, list), "Case library response must be a list"
            if require_empty:
                assert cases == [], "Expected initial empty case library"
            students = client.get("/api/teacher/students", params={"token": token})
            students.raise_for_status()
            records = students.json()
            assert isinstance(records, list), "Student records response must be a list"
            if require_empty:
                assert records == [], "Expected initial empty student records"
            student_page = client.get("/student")
            asset = re.search(r'src="(/assets/[^\"]+)"', student_page.text)
            assert asset, "SPA HTML missing"
            assert client.get(asset[1]).status_code == 200, "SPA asset inaccessible"
            assert client.get("/api/student/cases").status_code in (401, 403), "Student login boundary missing"
            print(f"{row['id']}: key accepted; teacher/student SPA+JS reachable; {len(cases)} cases and {len(records)} student records readable", flush=True)
    print("PUBLIC READ-ONLY ACCEPTANCE PASSED (no model calls)", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--require-empty", action="store_true",
                        help="Require that all case libraries and student records are empty")
    args = parser.parse_args()
    check(args.url.rstrip("/"), args.config, require_empty=args.require_empty)
