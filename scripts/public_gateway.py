"""Public-test boundary. Each key routes to a separate loopback worker/database.

No application DB is imported here. Config is re-read on EVERY request so deleting
a key revokes existing sessions too. Raw keys never enter URLs or worker logs.
"""
import hashlib
import asyncio
from contextlib import suppress
import html
import json
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

COOKIE = "case_test_space"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade", "content-length", "content-encoding"}


def read_spaces(path: Path):
    rows = json.loads(path.read_text(encoding="utf-8"))["spaces"]
    result = {}
    keys = set()
    for row in rows:
        ident, key = row["id"], row["key"]
        if not isinstance(ident, str) or not ident.isascii() or not ident.replace("-", "").isalnum():
            raise ValueError("Invalid space id")
        if not isinstance(key, str) or len(key) < 24 or key in keys or ident in result:
            raise ValueError("Invalid or duplicate key/space")
        keys.add(key)
        result[ident] = row
    return result


def digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def safe_next(value):
    if value.startswith(("/teacher", "/student")) and not any(c in value for c in ("\\", "\r", "\n", "<", '"')):
        return value
    return "/student"


def gate_page(next_path, error=""):
    return HTMLResponse("""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>案例推演 · 测试空间</title>
<style>body{font-family:system-ui;background:#f4f7fc;margin:0;padding:24px}main{max-width:440px;margin:10vh auto;background:white;padding:32px;border-radius:16px}input,button{box-sizing:border-box;width:100%;padding:14px;margin:10px 0;font-size:16px}button{background:#1677ff;color:white;border:0;border-radius:8px}p{line-height:1.7;color:#555}</style>
<main><h1>进入测试空间</h1><p>请输入管理员发给你的测试密钥。同一密钥的教师端和学生端共享案例，不同密钥的数据相互隔离。学生进入后可注册个人账号，以便教师查看作答。</p>
<p role="alert" style="color:#c33">""" + html.escape(error) + """</p><form method="post" action="/access/login">
<input type="hidden" name="next" value=""" + '"' + html.escape(safe_next(next_path), quote=True) + '"' + """>
<input type="password" name="key" maxlength="160" placeholder="测试密钥" required autofocus autocomplete="off">
<button>使用密钥进入</button></form></main></html>""")


def bootstrap(token, target, clear=False):
    # Tokens are generated server-side; json.dumps avoids executable interpolation.
    return HTMLResponse('<!doctype html><meta charset="utf-8"><title>正在进入</title><script>'
        + ('sessionStorage.clear();' if clear else '')
        + 'sessionStorage.setItem("teacher_token",' + json.dumps(token) + ');location.replace('
        + json.dumps(safe_next(target)).replace('<', '\\u003c') + ');</script>')


def create_gateway(config_path: Path, workers: dict, public_origin="", secure=True):
    sessions = {}
    attempts = defaultdict(deque)
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5), trust_env=False)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request, call_next):
        origin = request.headers.get("origin")
        expected = public_origin or str(request.base_url).rstrip("/")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != expected:
            return JSONResponse({"detail": "来源不匹配"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def route(request: Request, path: str):
        try:
            spaces = read_spaces(config_path)
        except (OSError, ValueError, KeyError, TypeError):
            return JSONResponse({"detail": "测试配置无效，请联系管理员"}, 503)
        if path == "access/login" and request.method == "GET":
            return gate_page(request.query_params.get("next", "/student"))
        if path == "access/logout":
            sessions.pop(request.cookies.get(COOKIE), None)
            response = RedirectResponse("/access/login", 303)
            response.delete_cookie(COOKIE)
            return response
        if path == "access/login" and request.method == "POST":
            ip = request.headers.get("cf-connecting-ip") if public_origin else request.client.host
            queue = attempts[ip]
            now = time.monotonic()
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= 15:
                return JSONResponse({"detail": "尝试过于频繁，请一分钟后重试"}, 429)
            queue.append(now)
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 4096:
                    return JSONResponse({"detail": "登录请求过大"}, 413)
            fields = parse_qs(data.decode("utf-8", errors="replace"))
            key = fields.get("key", [""])[0]
            target = safe_next(fields.get("next", ["/student"])[0])
            row = next((r for r in spaces.values() if secrets.compare_digest(digest(r["key"]), digest(key))), None)
            if row is None or row["id"] not in workers:
                return gate_page(target, "密钥无效或已被删除")
            worker = workers[row["id"]]
            try:
                login = await client.post(worker["url"] + "/api/teacher/login", json={"password": worker["password"]})
                login.raise_for_status()
            except httpx.HTTPError:
                return JSONResponse({"detail": "测试空间尚未就绪，请联系管理员"}, 503)
            token = secrets.token_urlsafe(32)
            sessions.pop(request.cookies.get(COOKIE), None)
            sessions[token] = (row["id"], digest(key), login.json()["teacher_token"])
            response = bootstrap(sessions[token][2], target, clear=True)
            response.set_cookie(COOKIE, token, httponly=True, secure=secure, samesite="lax")
            print("[access] login accepted space=" + row["id"], flush=True)
            return response
        session = sessions.get(request.cookies.get(COOKIE))
        if not session or session[0] not in spaces or not secrets.compare_digest(session[1], digest(spaces[session[0]]["key"])):
            if path.startswith("api/"):
                return JSONResponse({"detail": "测试密钥未登录或已撤销，请重新打开教师端或学生端网址", "code": "test_access_required"}, 401)
            return gate_page("/" + path + ("?" + request.url.query if request.url.query else ""))
        if path == "teacher/login":
            return bootstrap(session[2], "/teacher")
        if path == "api/teacher/login":
            return JSONResponse({"detail": "请使用测试密钥入口登录"}, 403)
        if path in ("docs", "redoc", "openapi.json"):
            return Response(status_code=404)
        worker = workers[session[0]]
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP | {"host", "cookie", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"}}
        # Fixed verified origin, not caller-controlled forwarded headers. QR uses it.
        origin = httpx.URL(public_origin or str(request.base_url))
        headers["host"] = origin.netloc.decode()
        headers["x-forwarded-proto"] = origin.scheme
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 30 * 1024 * 1024:
                return JSONResponse({"detail": "上传大小上限为 30 MB"}, 413)
        try:
            if path.startswith("api/play/") and path.endswith("/chat"):
                upstream = await client.send(client.build_request(request.method, worker["url"] + "/" + path,
                    params=request.query_params.multi_items(), headers=headers, content=bytes(body)), stream=True)
                async def chunks():
                    iterator = upstream.aiter_bytes().__aiter__()
                    pending = None
                    try:
                        while True:
                            pending = asyncio.create_task(anext(iterator))
                            while not pending.done():
                                done, _ = await asyncio.wait({pending}, timeout=15)
                                if not done:
                                    yield b": keepalive\n\n"
                            try:
                                yield pending.result()
                            except StopAsyncIteration:
                                break
                    finally:
                        if pending and not pending.done():
                            pending.cancel()
                            with suppress(asyncio.CancelledError):
                                await pending
                        await upstream.aclose()
                # Quick Tunnels do not support EventSource/SSE. The existing fetch
                # reader accepts the same framed payload as a chunked text response.
                return StreamingResponse(chunks(), status_code=upstream.status_code,
                    media_type="text/plain" if upstream.status_code == 200 else "application/json")
            reply = await client.request(request.method, worker["url"] + "/" + path,
                params=request.query_params.multi_items(), headers=headers, content=bytes(body))
        except httpx.HTTPError:
            return JSONResponse({"detail": "本机测试服务暂时不可用，请联系管理员"}, 502)
        return Response(reply.content, reply.status_code,
            headers={k: v for k, v in reply.headers.items() if k.lower() not in HOP | {"set-cookie"}})

    return app
