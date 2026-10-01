"""LLM 调用层：统一走 OpenAI 兼容的 /chat/completions。

只依赖 httpx，不引入 langchain / openai sdk。
对外只暴露一个入口 chat_json()，负责：发请求 → 解析 JSON → 缺键重试 → 抛 LLMFailed。
"""
import asyncio
import json
import os
import uuid
import time
from urllib.parse import urlsplit
from typing import Any, Dict, List

import httpx
from contracts.telemetry import event
from contracts.telemetry import log
from contracts.telemetry import output_summary
from contracts.telemetry import redact

# Default per-read timeout. The effective default is selected per stage below;
# the total wall-clock deadline is still enforced independently.
REQUEST_TIMEOUT = 60.0


from contracts.errors import LLMFailed, ValidationExhausted

# Output budgets include room for reasoning. Stage settings never change model choice.
PROFILES = {
    'default': (4096, 120), 'case_generation': (12288, 180),
    'case_baseline': (8192, 150),
    'impact_rules': (8192, 150),
    'review_generation': (8192, 150), 'review_check': (4096, 90),
    'tutor_plan': (2048, 45), 'tutor_answer': (4096, 90),
    'teacher_fix': (4096, 90), 'legacy_decision': (4096, 90),
}


def _bounded_setting(name, default, lower, upper):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise LLMFailed(f'{name} 必须为整数', retryable=False)
    if not lower <= value <= upper:
        raise LLMFailed(f'{name} 必须在 {lower}–{upper} 之间', retryable=False)
    return value


def _settings() -> Dict[str, str]:
    """从环境变量取配置；不读取 .env 文件，由启动方自行注入。

    LLM_* 是项目统一配置名，同时接受 DeepSeek 文档常见的 DEEPSEEK_API_KEY
    别名，避免把供应商字段泄漏到业务代码中。
    """
    return {
        "provider": (os.getenv("LLM_PROVIDER") or "openai_compatible").strip(),
        "base": (os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "").strip().rstrip("/"),
        "key": (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip(),
        "model": (os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "").strip(),
    }


def _build_retry_message(required: List[str], reason: str) -> Dict[str, str]:
    """重试时补一条纠偏消息，比原样重发更容易救回来。"""
    return {
        "role": "user",
        "content": (
            "上一次输出不满足要求（原因：%s）。请只返回一个合法的 JSON 对象，"
            "不要包裹 Markdown 代码块，且必须包含以下顶层键：%s。"
            % (reason, "、".join(required))
        ),
    }


async def chat_json(
    messages: List[Dict[str, str]],
    schema: Dict[str, Any],
    retries: int = 2,
    *, reasoning_effort: str | None = None, stage: str = 'default',
) -> Dict[str, Any]:
    """调模型并返回解析后的 JSON 对象。

    messages: OpenAI 兼容的对话数组。
    schema  : 只用于声明**顶层必需键**（取 schema.keys()），不做完整 JSON Schema 校验；
              深层结构校验由 services/validator.py 负责。
    retries : 允许的重试次数，总尝试次数为 retries + 1。

    失败时抛 LLMFailed，消息里不含任何密钥信息。
    """
    cfg = _settings()
    # 没配 Key 就直接失败，绝不发网络请求（否则会卡在超时上）
    if not cfg["key"]:
        log.error("模型配置缺失：LLM_API_KEY；未发出外部请求")
        raise LLMFailed("未配置 LLM_API_KEY，已跳过模型调用", retryable=False)
    if not cfg["base"]:
        log.error("模型配置缺失：LLM_BASE_URL；未发出外部请求")
        raise LLMFailed("未配置 LLM_BASE_URL，已跳过模型调用", retryable=False)
    if not cfg["model"]:
        log.error("模型配置缺失：LLM_MODEL；未发出外部请求")
        raise LLMFailed("未配置 LLM_MODEL，已跳过模型调用", retryable=False)

    required = list(schema.keys())
    if cfg["provider"] != "openai_compatible":
        raise LLMFailed("暂不支持 LLM_PROVIDER=%s，仅支持 openai_compatible" % cfg["provider"], retryable=False)

    if stage not in PROFILES:
        supported = ", ".join(sorted(PROFILES))
        log.error("模型配置错误：未知模型任务阶段 stage=%s supported=%s；未发出外部请求",
                  stage, supported)
        raise LLMFailed('未知模型任务阶段：%s' % stage, retryable=False)
    tokens, seconds = PROFILES[stage]
    max_tokens = _bounded_setting('LLM_' + stage.upper() + '_MAX_TOKENS', tokens, 512, 32768)
    deadline = _bounded_setting('LLM_' + stage.upper() + '_TIMEOUT', seconds, 10, 600)
    # A short httpx read timeout used to cut off case_generation at 60 seconds
    # even though its workflow deadline is 180 seconds. Only an explicit env
    # override should shorten the provider read window below the stage deadline.
    timeout = _bounded_setting('LLM_READ_TIMEOUT', max(10, deadline), 10, 600)
    deepseek = urlsplit(cfg['base']).hostname == 'api.deepseek.com'
    default_effort = (os.getenv('LLM_REASONING_EFFORT') or 'low') if deepseek else None
    effort = os.getenv('LLM_' + stage.upper() + '_REASONING_EFFORT') or reasoning_effort or default_effort
    if effort is not None and effort not in {'low', 'high', 'max'}:
        raise LLMFailed('不支持的思考强度', retryable=False)

    url = "%s/chat/completions" % cfg["base"]
    request_id = uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer %s" % cfg["key"],
        "Content-Type": "application/json",
        "X-Client-Request-Id": request_id,
    }

    convo: List[Dict[str, str]] = list(messages)
    last_error = "未知原因"
    retryable = False

    for attempt in range(retries + 1):
        started = time.perf_counter()
        event("模型调用开始", call_id=request_id, model=cfg["model"],
              host=urlsplit(cfg["base"]).hostname, operation="chat/completions",
              attempt=attempt + 1, limit=retries + 1, expected_keys=required,
              stage=stage, reasoning_effort=effort, max_tokens=max_tokens, deadline_seconds=deadline,
              input_characters=sum(len(m.get("content", "")) for m in convo))
        body = {
            "model": cfg["model"],
            "messages": convo,
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
            "max_tokens": max_tokens,
        }
        if effort is not None:
            body["reasoning_effort"] = effort
            if deepseek:
                body["thinking"] = {"type": "enabled"}
        retryable = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, float(timeout)))) as client:
                response = await asyncio.wait_for(client.post(url, json=body, headers=headers), timeout=deadline)

            if response.status_code >= 400:
                # 只记状态码，不回显响应体（可能含敏感信息）
                last_error = "模型接口返回 HTTP %d" % response.status_code
                # Only the provider's error object, never headers or request bodies.
                try:
                    provider_error = response.json().get("error", {})
                    if isinstance(provider_error, dict):
                        last_error += "；" + redact(str(provider_error.get("message", "")))[:500]
                except (ValueError, AttributeError):
                    pass
                retryable = response.status_code in {408, 429} or response.status_code >= 500
                if not retryable:
                    raise LLMFailed(last_error, retryable=False)
            else:
                data = response.json()
                choices = data.get("choices") or []
                content = ""
                if choices:
                    content = (choices[0].get("message") or {}).get("content") or ""
                    finish = choices[0].get('finish_reason')
                    if finish == 'length':
                        max_tokens = min(32768, max_tokens * 2)
                        raise ValueError('模型输出被截断；请精简表达并保留全部必填内容')
                    if finish not in (None, 'stop'):
                        raise LLMFailed('模型未正常完成：' + str(finish), retryable=False)
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    last_error = "模型输出的顶层不是 JSON 对象"
                else:
                    missing = [k for k in required if k not in parsed]
                    if missing:
                        last_error = "模型输出缺少顶层键：%s" % "、".join(missing)
                    else:
                        event("模型调用成功", call_id=request_id,
                              stage=stage, usage={k: v for k, v in (data.get('usage') or {}).items()
                                                 if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and isinstance(v, int)},
                              elapsed_ms=round((time.perf_counter() - started) * 1000),
                              output_characters=len(content), output=output_summary(parsed))
                        return parsed
        except json.JSONDecodeError as exc:
            last_error = "模型输出不是合法 JSON（%s）" % exc
        except LLMFailed as exc:
            log.warning('模型调用终止 call_id=%s stage=%s reason=%s retry=False', request_id, stage, exc)
            raise
        except ValueError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001 —— 网络/超时等一律降级为 LLMFailed
            retryable = isinstance(exc, (httpx.TransportError, TimeoutError))
            last_error = "模型调用失败：%s" % exc.__class__.__name__
            log.exception("模型网络/响应处理异常 call_id=%s", request_id)

        log.warning("模型调用失败 call_id=%s attempt=%s/%s reason=%s retry=%s",
                    request_id, attempt + 1, retries + 1, last_error, attempt < retries)
        if attempt < retries:
            convo = list(messages) if retryable else list(messages) + [_build_retry_message(required, last_error)]
            if retryable:
                await asyncio.sleep(min(2 ** attempt, 4))

    raise LLMFailed(last_error, retryable=retryable)
