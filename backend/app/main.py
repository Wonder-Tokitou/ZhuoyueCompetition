"""case-sim 后端入口。

职责（顺序即注册顺序，不能随意调换）：
1. 创建 FastAPI 实例；
2. 挂 GET /api/health 健康检查；
3. create_all 建表，并挂载教师端 / 学生端路由；
4. 把后端生成的资源目录 backend/app/static 挂到 /media（二维码图片等）；
5. 把前端构建产物 frontend/dist 挂到根路径 /。

注意：第 5 步挂在根路径 `/`，必须最后注册，否则会把前面注册的接口一起吞掉。
"""
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 加载 .env 环境变量（backend/.env）
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from backend.app.observability import configure_logging
from backend.app.observability import event
from backend.app.observability import RequestLogMiddleware
configure_logging()

from backend.app import models  # noqa: F401 —— 导入以注册全部业务与任务模型
from backend.app.db import Base
from backend.app.db import engine
from backend.app.migrations import migrate
from backend.app.routers import accounts
from backend.app.routers import student
from backend.app.routers import teacher

# 前端构建产物目录：backend/app/main.py -> 上溯三级即 monorepo 根
DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
# 后端自己生成的资源（二维码等）。复用 teacher 的常量，保证「挂载的目录」与
# 「写入二维码的目录」永远是同一个，避免两处各算一遍路径而漂移。
ASSETS_DIR = teacher.STATIC_DIR

# 1) 创建 FastAPI 实例
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 任务记录已持久化；服务重启后恢复上次未完成的 AI 工作流。
    from backend.app.integrations.ai.task_runner import recover_pending_tasks

    # 启动自检：明确令牌存储口径，便于运维在日志里确认「多 worker 踢出」已根治。
    event("启动自检",
          teacher_token_store="database(teacher_session)+process cache",
          process_memory_state="student_try_store,ai_task_registry",
          media_mount=str(ASSETS_DIR),
          note="后两项仍是进程内存，部署必须保持 gunicorn --workers 1")
    await recover_pending_tasks()
    yield


app = FastAPI(title="case-sim", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestLogMiddleware)

# Validation errors must not log Pydantic's "input" (may contain passwords/files).
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler, http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    event("请求参数校验失败", level=logging.WARNING,
          errors=[{"loc": e["loc"], "type": e["type"]} for e in exc.errors()])
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()]})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    event("业务请求失败", level=logging.WARNING, status=exc.status_code, reason=exc.detail)
    return await http_exception_handler(request, exc)


# 2) 健康检查：固定返回 {"status": "ok"}，用于联调与部署自检
@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


# 3) 建表（初赛用 create_all，不引入 alembic）+ 挂载 15 条接口
Base.metadata.create_all(bind=engine)
migrate()
app.include_router(teacher.router)
app.include_router(student.router)
app.include_router(accounts.router)


# 4) 挂载后端资源目录（二维码等）。
# ⚠️ 必须先建目录、再无条件下挂载，不能写成 `if ASSETS_DIR.is_dir()` 静默跳过：
# 目录不存在时跳过挂载，/media/* 会落到第 5 步的 SPA 兜底，返回 index.html
# （HTTP 200 但内容是网页而非图片），表现为「案例发布成功、二维码却打不开」。
# 而首次发布之前 backend/app/static/ 很可能还没被创建（二维码是发布时才写的）。
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(ASSETS_DIR)), name="media")


# 4b) 同一批制品的 /api 前缀等价入口。
# 线上 Nginx 若是 `root …/frontend/dist` + `try_files $uri $uri/ /index.html`
# 且只反代 `/api/`，那么 `/media/*` 会被 Nginx 自己按 try_files 兜底成 index.html
# （HTTP 200、Content-Type: text/html，但请求根本到不了后端）。`/api/` 则一定被
# 反代，所以发布接口返回的 qr_code_url 走这个入口 —— 二维码在任何「只反代 /api」
# 的部署拓扑下都可达，不必依赖 Nginx 配置完美。
@app.get("/api/media/{path:path}")
async def api_media(path: str):
    root = ASSETS_DIR.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(target)


# 5) 前端静态资源 + SPA catch-all（替换 StaticFiles mount，解决 /teacher/{token} 等 SPA 路由 404）
# StaticFiles(html=True) 只对目录路径回退到 index.html，对 SPA 路由路径（如 /teacher/{token}）返回 404
# 改为自定义路由：先尝试返回静态文件，找不到则回退到 index.html
INDEX_HTML = DIST_DIR / "index.html" if DIST_DIR.is_dir() else None


@app.get("/{full_path:path}")
async def spa_serve(full_path: str):
    if INDEX_HTML is None:
        return HTMLResponse(content="frontend not built", status_code=404)
    # 先尝试作为静态文件返回
    file_path = (DIST_DIR / full_path).resolve()
    if not file_path.is_relative_to(DIST_DIR.resolve()):
        return JSONResponse(status_code=404, content={"detail": "资源不存在"})
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "接口不存在"})
    if file_path.is_file():
        return FileResponse(file_path)
    # SPA 路由回退到 index.html
    return FileResponse(INDEX_HTML)
