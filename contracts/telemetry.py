"""Shared structured events and redaction; the host installs log handlers."""
import contextvars
import json
import logging
import os
import re

request_id = contextvars.ContextVar("request_id", default="-")
task_id = contextvars.ContextVar("task_id", default="-")
log = logging.getLogger("case_sim")


def redact(value):
    text = str(value)
    for key, secret in os.environ.items():
        if any(word in key.upper() for word in ("KEY", "PASSWORD", "TOKEN", "SECRET")) and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s'\"<>,]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)((?:api_key|token|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s&,\"'}]+", r"\1[REDACTED]", text)
    text = re.sub(r"(/(?:api/play|student)/)[^/?\s\"']+", r"\1[REDACTED]", text)
    text = re.sub(r"(/teacher/)(?!login\b)[A-Za-z0-9_-]{20,}", r"\1[REDACTED]", text)
    text = re.sub(r"(?is)\[parameters:.*", "[parameters: REDACTED]", text)
    return text


def event(name, level=logging.INFO, **fields):
    # Do not pass request bodies, prompts, passwords, or student names here.
    log.log(level, "%s %s", name, json.dumps(fields, ensure_ascii=False, default=str))


def output_summary(value):
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    result = {"keys": list(value)}
    for key in ("nodes", "dimensions"):
        if isinstance(value.get(key), list):
            result[key + "_count"] = len(value[key])
    if isinstance(value.get("after_metrics"), dict):
        result["after_metrics"] = value["after_metrics"]
    # Explicit opt-in: model text may contain teaching materials/personal data.
    if os.getenv("LOG_AI_CONTENT", "0") == "1":
        result["preview"] = redact(json.dumps(value, ensure_ascii=False))[:2000]
    return result
