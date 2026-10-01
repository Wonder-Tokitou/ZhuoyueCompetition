"""Student-visible strategy labels never reveal teacher risk buckets."""
import re

_RISK_WORDS = re.compile(r"保守|稳健|激进")


def student_label(label: str, key: str) -> str:
    """学生端只展示策略内容，不泄露教师端的风险分档。"""
    value = _RISK_WORDS.sub("", str(label or ""))
    value = re.sub(r"^[\s:：、，,。；;（）()\[\]【】\-—]+", "", value)
    value = re.sub(r"[\s:：、，,。；;（）()\[\]【】\-—]+$", "", value)
    return value.strip() or ("方案 " + key)
