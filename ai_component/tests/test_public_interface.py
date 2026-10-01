"""Exercise the AI module through data/capability inputs, without importing the host."""
import asyncio
import copy
from unittest.mock import AsyncMock

from ai_component import StudentReviewAgent, TutorAgent, generate_valid_case, suggest_fix
from ai_component.prompts.case_generation import build_messages
from contracts.ai import ReviewInput, TeachingPolicy
from contracts.errors import LLMFailed, ValidationExhausted
import pytest


def test_review_accepts_plain_data_and_independent_model_adapter():
    request = ReviewInput({"decisions": [{"strategy": "渠道转型"}]}, "测试框架", ["成本", "收益", "综合结论"])
    dimensions = [{"name": n, "content": "基于本次选择的分析"} for n in ["收益", "补充风险", "成本"]]
    model = AsyncMock(side_effect=[{"dimensions": dimensions, "conclusion": "分阶段投入"},
                                   {"passed": True, "issues": []}])
    records = []
    before = copy.deepcopy(request)
    result = asyncio.run(StudentReviewAgent(model).run(request, lambda *x: records.append(x)))
    assert result["dimensions"] == dimensions and request == before
    assert [r[0] for r in records] == ["prepare_evidence", "review_candidate", "structure_check", "independent_review_check"]
    assert model.call_args_list[0].args[0] is not model.call_args_list[1].args[0]


def test_missing_baseline_exhausts_bounded_repairs():
    model = AsyncMock(return_value={"dimensions": [{"name": "补充", "content": "有内容"}], "conclusion": "结论"})
    with pytest.raises(ValidationExhausted, match="缺少基础"):
        asyncio.run(StudentReviewAgent(model).run(ReviewInput({}, "测试", ["成本"]), lambda *_: None))
    assert model.await_count == 3


class MemoryTutorTools:
    """A second capability adapter: no SQL, files, credentials or external search."""
    def __init__(self):
        self.reads, self.saved, self.searches = [], [], []

    def read(self, names):
        self.reads.append(names)
        return [{"id": "read_case", "title": "测试案例", "version": 1, "content": {"background": "渠道选择"}}]

    async def search(self, query):
        self.searches.append(query)
        return [], ""

    def record(self, data):
        self.saved.append(data)


def test_tutor_uses_scoped_capabilities_and_keeps_raw_trace():
    model = AsyncMock(side_effect=[
        {"business": True, "tools": ["read_case"], "search_query": ""},
        {"answer": "结合渠道选择。[read_case]", "source_ids": ["read_case"]}])
    tools = MemoryTutorTools()
    answer = asyncio.run(TutorAgent(model).run("如何选择渠道？", tools))
    assert "【案例资料】" in answer and "read_case" not in answer
    assert tools.reads and tools.saved[0]["answer"]["source_ids"] == ["read_case"]
    assert not tools.searches


def test_tutor_rejects_unknown_tool_before_host_access():
    tools = MemoryTutorTools()
    model = AsyncMock(return_value={"business": True, "tools": ["read_all_students"], "search_query": ""})
    with pytest.raises(LLMFailed):
        asyncio.run(TutorAgent(model).run("资料", tools))
    assert not tools.reads and not tools.saved


def test_case_generation_uses_injected_policy_and_business_validator():
    policy = TeachingPolicy([], [], [], {"测试类型": "测试框架"}, {"测试框架": ["成本"]},
                            ("revenue", "gross_margin", "market_share", "cash_flow"))
    model = AsyncMock(side_effect=[{"title": "原稿"}, {"title": "修复稿"}])
    checks = []
    def validate(candidate):
        checks.append(copy.deepcopy(candidate))
        return (False, ["缺少节点"]) if len(checks) == 1 else (True, [])
    payload, errors = asyncio.run(generate_valid_case("素材", "material", "测试类型", None,
                                 policy=policy, validate=validate, model=model))
    assert not errors and payload["title"] == "修复稿"
    assert checks[0]["review"] == [{"name": "成本", "content": ""}]
    assert "原稿" in model.call_args_list[1].args[0][1]["content"]


def test_case_generation_system_prompt_preserves_teacher_scope():
    """金额口径回归：教师确认的范围优先，不能强制变为单店。"""
    policy = TeachingPolicy([], [], [], {}, {}, ("revenue", "gross_margin", "market_share", "cash_flow"))
    system = build_messages("素材", "material", "测试类型", "模板", policy=policy)[0]["content"]
    assert "若已提供教师基准，必须保持其经营范围和时间口径" in system
    assert "未提供教师已确认基准时" in system


def test_teacher_advice_only_returns_a_suggestion():
    model = AsyncMock(return_value={"reply": "建议降低营销投入"})
    context = ["教师确认案例"]
    assert asyncio.run(suggest_fix(context, "如何优化", model=model)) == {"reply": "建议降低营销投入"}
    assert context == ["教师确认案例"]
