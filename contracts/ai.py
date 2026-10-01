"""Versioned data and capability contracts; no ORM, HTTP routes, or provider imports."""
from dataclasses import dataclass
from typing import Any, Callable, Protocol

CONTRACT_VERSION = 1
NODE_ROLES = ["核心战略", "核心策略", "落地执行"]

@dataclass(frozen=True)
class TeachingPolicy:
    CASE_TYPES: list[dict]
    INDUSTRY_BASELINE: list[dict]
    TRANSMISSION_RULES: list[dict]
    CASE_TYPE_TO_FRAMEWORK: dict[str, str]
    FRAMEWORK_DIMENSIONS: dict[str, list[str]]
    METRIC_KEYS: tuple[str, ...]

@dataclass(frozen=True)
class ReviewInput:
    evidence: dict[str, Any]
    framework: str
    dimensions: list[str]

class TutorTools(Protocol):
    """Host binds these capabilities to one authorized session; no model-provided IDs."""
    def read(self, names: list[str]) -> list[dict]: ...
    async def search(self, query: str) -> tuple[list[dict], str]: ...
    def record(self, data: dict) -> None: ...

class JsonModel(Protocol):
    async def __call__(self, messages: list[dict], schema: dict, retries: int = 1, **options: Any) -> dict: ...

Record = Callable[[str, Any], None]
