"""UTF-8 runtime logs with safe summaries and request/task correlation."""
import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import time
import traceback
import uuid

from contracts.telemetry import request_id, task_id, log, redact, event, output_summary


class SafeFormatter(logging.Formatter):
    def format(self, record):
        # Never mutate a shared record or dump traceback locals.
        message = redact(record.getMessage()).replace("\r", "\\r").replace("\n", "\\n")
        prefix = (f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} "
                  f"{record.levelname:<7} [{record.name}] "
                  f"request={request_id.get()} task={task_id.get()} ")
        if record.exc_info:
            typ, error, tb = record.exc_info
            frames = "".join(traceback.format_list(traceback.extract_tb(tb)))
            message += "\n" + redact(frames) + typ.__name__ + ": " + redact(error)
        return prefix + message


def configure_logging():
    root_dir = Path(__file__).resolve().parents[2]
    path = Path(os.getenv("CASE_SIM_LOG_FILE") or root_dir / "logs" / "runtime.log")
    if not path.is_absolute():
        path = root_dir / path
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    current = next((h for h in root.handlers if getattr(h, "_case_sim", False)), None)
    if current is not None and Path(getattr(current, "_case_sim_path", "")).resolve() == path.resolve():
        return path
    # Tests, embedded workers and local reloads can change CASE_SIM_LOG_FILE.
    # Rebind only our handlers; never remove application-owned handlers.
    for old in list(root.handlers):
        if getattr(old, "_case_sim", False):
            old.close()
            root.removeHandler(old)
    formatter = SafeFormatter()
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    console = logging.StreamHandler()
    for item in (handler, console):
        item.setFormatter(formatter)
        item._case_sim = True
        item._case_sim_path = str(path)
        root.addHandler(item)
    root.setLevel(logging.INFO)
    # Uvicorn access records include raw URL query tokens. Our middleware logs
    # route templates instead. Route all errors through the safe formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    event("日志已启动", file=str(path), content_preview=os.getenv("LOG_AI_CONTENT", "0") == "1")
    return path


class RequestLogMiddleware:
    """Pure ASGI: do not consume uploads or buffer SSE response streams."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        context = request_id.set(uuid.uuid4().hex[:12])
        started, status = time.perf_counter(), 500
        event("HTTP请求开始", method=scope["method"],
              side="学生端" if scope["path"].startswith("/api/play/") else "教师端/系统")

        async def send_logged(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", request_id.get().encode("ascii"))
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_logged)
        except Exception:
            log.exception("请求未处理异常 method=%s", scope["method"])
            raise
        finally:
            route = getattr(scope.get("route"), "path", "<unmatched>")
            event("HTTP请求完成", level=logging.WARNING if status >= 400 else logging.INFO,
                  method=scope["method"], route=route, status=status,
                  elapsed_ms=round((time.perf_counter() - started) * 1000))
            request_id.reset(context)
