import asyncio
import qrcode
import pytest
from PIL import Image
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app import models
from backend.app.db import get_db
from backend.app.main import app
from backend.app.routers import teacher
from backend.app.seed import seed_demo_case
from backend.tests.test_mvp_smoke import temporary_db
from backend.tests.teacher_helpers import login_teacher


def test_qrcode_backend_can_render_png(tmp_path):
    output = tmp_path / "probe.png"
    qrcode.make("http://testserver/student/probe").save(output)
    with Image.open(output) as image:
        assert image.format == "PNG"
        image.verify()


@pytest.mark.parametrize("failure", [
    ModuleNotFoundError("No module named 'PIL'"),
    PermissionError("QR directory is not writable"),
])
def test_publish_does_not_persist_when_qrcode_generation_fails(tmp_path, monkeypatch, failure):
    _, factory = temporary_db(tmp_path)
    with factory() as db:
        case = seed_demo_case(db)
        case.status = "ready"
        case.published_at = None
        db.commit()
        case_id = case.id

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    def fail_qrcode(*_args, **_kwargs):
        raise failure

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(qrcode, "make", fail_qrcode)
    monkeypatch.setattr(teacher, "QRCODE_DIR", tmp_path / "qrcode")
    client = TestClient(app)
    token = login_teacher(client, monkeypatch)
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            f"/api/cases/{case_id}/publish?token={token}"
        )
        assert response.status_code == 500
        with factory() as db:
            persisted = db.get(models.Case, case_id)
            assert persisted.status == "ready"
            assert persisted.published_at is None
    finally:
        app.dependency_overrides.clear()


def test_publish_returns_real_png_and_retry_preserves_publication(tmp_path, monkeypatch):
    _, factory = temporary_db(tmp_path)
    with factory() as db:
        case = seed_demo_case(db)
        case.status = "ready"
        case.published_at = None
        db.commit()
        case_id, student_token = case.id, case.student_token

    def override_db():
        with factory() as db:
            yield db

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(teacher, "QRCODE_DIR", tmp_path / "qrcode")
    # /api/media 路由从 main.ASSETS_DIR 取根目录，测试要把制品库整体搬到 tmp_path，
    # 否则二维码写到了 tmp_path 而路由还去真实 static/ 里找。
    monkeypatch.setattr(main_module, "ASSETS_DIR", tmp_path)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    client = TestClient(app)
    token = login_teacher(client, monkeypatch)
    try:
        endpoint = f"/api/cases/{case_id}/publish?token={token}"
        response = client.post(endpoint)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "student_url": f"/student/{student_token}",
            "qr_code_url": f"/api/media/qrcode/{student_token}.png",
        }
        # 该路径必须真的能取到 PNG（线上 Nginx 只反代 /api/，所以二维码走这个前缀）
        served = client.get(f"/api/media/qrcode/{student_token}.png")
        assert served.status_code == 200, served.text
        assert served.headers["content-type"].startswith("image/png")
        # 不存在的文件必须 404，而不是落回 SPA 的 index.html
        assert client.get("/api/media/qrcode/does-not-exist.png").status_code == 404
        # 目录穿越必须被 is_relative_to 拦住：root 之外即使文件真实存在也不能取到
        outside = tmp_path.parent / "outside-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        with pytest.raises(HTTPException) as blocked:
            asyncio.run(main_module.api_media("../outside-secret.txt"))
        assert blocked.value.status_code == 404
        with Image.open(tmp_path / "qrcode" / f"{student_token}.png") as image:
            assert image.format == "PNG"
            image.verify()
        with factory() as db:
            case = db.get(models.Case, case_id)
            assert case.status == "published"
            published_at = case.published_at
            assert published_at is not None
        assert client.post(endpoint).json() == response.json()
        with factory() as db:
            assert db.get(models.Case, case_id).published_at == published_at
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
