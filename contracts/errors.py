class LLMFailed(RuntimeError):
    """模型不可用、返回不是合法 JSON、或缺少必需键（重试耗尽后）时抛出。"""

    def __init__(self, message, *, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class ValidationExhausted(RuntimeError):
    """Bounded semantic repair has finished; do not restart the entire workflow."""
