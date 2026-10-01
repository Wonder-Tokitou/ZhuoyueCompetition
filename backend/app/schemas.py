"""Pydantic v2 模型：SPEC.md §3 里出现的每一个请求体与响应体。

枚举一律用 Python Enum 约束；取值与 docs/商科规则.json / docs/标杆案例.json 对齐，
对齐关系在 app/domain/rules.py 启动时校验。
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────── 枚举 ───────────────────────────────

class CaseStatus(str, Enum):
    """case.status 四态。"""

    draft = "draft"
    generating = "generating"
    ready = "ready"
    published = "published"


class CaseType(str, Enum):
    """案例类型（取自商科规则.json 的 case_types.case_type）。"""

    strategy = "战略决策类"
    marketing = "市场营销类"
    finance = "财务管理类"


class SourceKind(str, Enum):
    """素材来源。"""

    material = "material"
    topic = "topic"


class OwnerType(str, Enum):
    """案例归属。"""

    teacher = "teacher"
    student_ephemeral = "student_ephemeral"


class NodeRole(str, Enum):
    """节点角色，按 idx 1/2/3 固定对应。"""

    core_strategy = "核心战略"
    core_tactic = "核心策略"
    execution = "落地执行"


class RiskLevel(str, Enum):
    """选项风险等级，每个节点三档各一个。"""

    conservative = "保守"
    balanced = "稳健"
    aggressive = "激进"


class OptionKey(str, Enum):
    """选项标识。"""

    A = "A"
    B = "B"
    C = "C"


class ReviewFramework(str, Enum):
    """复盘框架（取自商科规则.json 的 review_templates.template）。"""

    swot = "SWOT分析模型"
    four_p = "4P营销理论"
    break_even = "盈亏平衡分析"


class ResultSource(str, Enum):
    """回合结果来源；写入 turn.result_source，并在 S2 的 result.source 返回。"""

    preset_option = "preset"
    progressive = "progressive"
    custom_input = "custom_llm"
    fallback_balanced = "fallback_balanced"


class MessageRole(str, Enum):
    """对话消息角色。"""

    user = "user"
    assistant = "assistant"
    system = "system"


# ─────────────────────────────── 基类 ───────────────────────────────

class Schema(BaseModel):
    """所有出入参的基类：允许直接由 ORM 对象构造。"""

    model_config = ConfigDict(from_attributes=True)


# ─────────────────────────── 通用子结构 ───────────────────────────

class Metrics(Schema):
    """指标固定四键，口径见 SPEC.md §0.2 / 商科规则.json 的 transmission_rules。"""

    revenue: float = Field(description="单店月营收，单位万元")
    gross_margin: str = Field(description="毛利率，保留一位小数的百分比字符串")
    market_share: str = Field(description="市场份额，保留一位小数的百分比字符串")
    cash_flow: str = Field(description="现金流方向描述文案")


class MetricDelta(Schema):
    """S2 / T9 的差值对象；百分比先解析为百分点，现金流记录前后文字。"""

    revenue: float = Field(description="营收数值差，单位万元")
    gross_margin: float = Field(description="毛利率百分点差")
    market_share: float = Field(description="市场份额百分点差")
    cash_flow: dict[str, str] = Field(description="现金流方向变化：{from, to}")


class OptionOutcome(Schema):
    """选项后果对象：decide 响应里的 result，也是 turn.result_json 的形状。"""

    metrics: Metrics
    summary: str


class TeacherOption(Schema):
    """教师端选项：老师必须看到风险等级与后果才能校准。"""

    key: OptionKey
    label: str
    risk_level: RiskLevel
    metrics: Metrics
    net_profit: Optional[float] = None
    financial_assumptions: Optional[dict] = None
    financial_basis: Optional[dict] = None
    calculated_net_profit: Optional[float] = None
    summary: str


class OptionInput(Schema):
    """教师端提交的选项（T6 请求体元素）。"""

    key: OptionKey
    label: str
    risk_level: RiskLevel
    metrics: Metrics
    net_profit: Optional[float] = None
    financial_assumptions: Optional[dict] = None
    financial_basis: Optional[dict] = None
    summary: str


class StudentOption(Schema):
    """学生端选项：**只有 key 与 label**，不得夹带任何后果信息。"""

    key: OptionKey
    label: str


class ReviewDimension(Schema):
    """复盘维度元素 `{name, content}`；教师端与学生端共用。

    分析项数量与顺序由 AI 根据有效内容动态返回；综合结论单独存储。
    """

    name: str
    content: str


# ─────────────────────────── 节点 ───────────────────────────

class TeacherNode(Schema):
    """T4 的 nodes 元素。"""

    id: int
    idx: int
    scenario: str
    node_role: NodeRole
    title: str
    background: str
    options: List[TeacherOption] = []


class StudentNode(Schema):
    """S1 / S5 / S6 的 nodes 元素。"""

    id: int
    idx: int
    scenario: str
    node_role: NodeRole
    title: str
    background: str
    options: List[StudentOption] = []


# ─────────────────────────── 教师端 ───────────────────────────

class LoginRequest(Schema):
    """T1 请求体。"""

    password: str


class LoginResponse(Schema):
    """T1 响应体。"""

    teacher_token: str


class CreateCaseResponse(Schema):
    """T2 响应体；warning 与 errors 仅在生成失败时出现。"""

    case_id: int
    status: CaseStatus
    warning: Optional[str] = None
    errors: Optional[List[str]] = None
    task_id: Optional[str] = None


class CaseSummary(Schema):
    """T3 响应体的裸数组元素。"""

    id: int
    title: str
    status: CaseStatus
    created_at: datetime


class CaseDetail(Schema):
    """T4 的 case 对象，对应 case 表全部 16 列（含本次新增的 version）。"""

    id: int
    title: str
    source_text: str
    status: CaseStatus
    framework_id: Optional[int] = None
    teacher_token: str
    student_token: str
    created_at: datetime
    published_at: Optional[datetime] = None
    case_type: CaseType
    source_kind: SourceKind
    base_metrics: Optional[Metrics] = None
    base_net_profit: Optional[float] = None
    financial_assumptions: Optional[dict] = None
    owner_type: OwnerType
    background: str
    dilemma: str
    version: int = 1


class CaseDetailResponse(Schema):
    """T4 响应体。"""

    case: CaseDetail
    nodes: List[TeacherNode] = []


class UpdateCaseRequest(Schema):
    """T5 请求体：两字段均可选，只传要改的。"""

    title: Optional[str] = None
    background: Optional[str] = None
    dilemma: Optional[str] = None
    base_metrics: Optional[Metrics] = None
    net_profit: Optional[float] = None
    financial_assumptions: Optional[dict] = None


class GenerateNodesRequest(Schema):
    base_metrics: Metrics
    financial_assumptions: dict
    net_profit: Optional[float] = None
    idempotency_key: Optional[str] = None


class OkResponse(Schema):
    """T5 / T6 响应体。"""

    ok: bool = True

class AiTaskResponse(Schema):
    task_id: str
    case_id: int
    status: str
    kind: str = "case_generation"
    stage: str = "queued"
    attempts: int = 0
    idempotency_key: Optional[str] = None
    error: Optional[str] = None
    output_url: Optional[str] = None


class AiTaskStepResponse(Schema):
    stage: str
    status: str
    attempt: int
    detail: Optional[dict] = None
    error: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None


class AiArtifactResponse(Schema):
    id: int
    kind: str
    path: str
    mime: str
    size: int
    sha256: str
    created_at: datetime

class AiTaskSubmitRequest(Schema):
    idempotency_key: Optional[str] = None

class ReviewPatchRequest(Schema):
    dimensions: Optional[List[ReviewDimension]] = None
    conclusion: Optional[str] = None


class NodePatchRequest(Schema):
    """T6 请求体：只传要改的字段。

    `scenario` 与 `background` 同源（见 SPEC §1.3），传任意一个都会同时写入两列。
    """

    scenario: Optional[str] = None
    title: Optional[str] = None
    background: Optional[str] = None
    options: Optional[List[OptionInput]] = None


class AiFixRequest(Schema):
    """T7 请求体。"""

    node_id: Optional[int] = None
    message: str


class AiFixResponse(Schema):
    """T7 响应体：只出建议，不落库。"""

    reply: str


class PublishResponse(Schema):
    """T8 响应体。"""

    student_url: str
    qr_code_url: str


class TurnItem(Schema):
    """T9 的 turns 元素。

    新增的四项指标列由 S2 写入；历史回合尚未回填时为 `null`（列尚未迁移完成前同样为 `null`）。
    `result_json` 保留为兼容字段，仍是最早的 `{metrics, summary}` 快照。
    """

    id: int
    node_id: int
    idx: int
    chosen_option: str
    display_option: Optional[str] = None
    chosen_label: Optional[str] = None
    input_text: Optional[str] = None
    before_metrics: Optional[Metrics] = None
    after_metrics: Optional[Metrics] = None
    delta_metrics: Optional[MetricDelta] = None
    result_source: Optional[ResultSource] = None
    result_json: OptionOutcome
    duration_ms: int
    created_at: datetime


class StudentRecord(Schema):
    """T9 响应体的裸数组元素：按案例、学生令牌、姓名与 attempt 归属分组。"""

    student_name: str
    student_id: Optional[int] = None
    attempt_no: int = 1
    turns: List[TurnItem] = []
    session_id: Optional[int] = None
    review_id: Optional[int] = None
    review_status: str = "not_started"
    review: Optional[dict] = None


# ─────────────────────────── 学生端 ───────────────────────────

class PlayResponse(Schema):
    """S1 响应体：一次下发全部节点，外加首屏所需的题面信息。

    红线：这里只下题面（标题/类型/背景/困境/基准数据），
    只有 S1 不得提前下发未选择选项的 risk_level / summary / metrics；
    检查必须匹配带引号的完整键名，禁止裸子串匹配。
    """

    case_title: str
    case_type: CaseType
    background: str
    dilemma: str
    base_metrics: Optional[Metrics] = None
    nodes: List[StudentNode] = []


class DecideRequest(Schema):
    """S2 请求体；字段顺序即契约顺序。

    `attempt_no` 不在请求体中；一次 attempt 归属 `(case_id, student_token, student_name)`，
    同一 session 刷新沿用当前 attempt，显式“再试一次”由服务端分配新的 attempt_no。
    """

    student_name: str
    session_id: Optional[int] = None
    node_id: int
    option_key: Optional[OptionKey] = None
    input_text: Optional[str] = None
    duration_ms: int = Field(ge=0, description="本回合决策耗时（毫秒），必填且非负")


class DecideResult(Schema):
    """S2 响应体里的 `result`：本次**已选择之后**的结果，不是提前泄露的答案。

    `after_metrics` 是该选项的「目标状态快照」，**不是可直接累加的增量**；
    `delta_metrics` 由服务端算 `after − before`。
    """

    before_metrics: Optional[Metrics] = None
    financial_state: Optional[dict] = None
    after_metrics: Optional[Metrics] = None
    delta_metrics: Optional[MetricDelta] = None
    summary: str
    source: ResultSource
    warning: Optional[str] = None


class DecideResponse(Schema):
    """S2 响应体。"""

    session_id: int
    result: DecideResult
    next_node_id: Optional[int] = None


class ChatRequest(Schema):
    """S3 请求体。"""

    session_id: int
    message: str = Field(min_length=1, max_length=4000)


class StartSessionRequest(Schema):
    student_name: str = Field(min_length=1, max_length=64)


class SessionState(Schema):
    financial_state: Optional[dict] = None
    session_id: int
    student_name: str
    case_version: int
    current_metrics: Metrics
    next_node_id: Optional[int] = None
    finished: bool
    play: PlayResponse
    turns: List[dict] = []


class ReviewResponse(Schema):
    path_comparison: Optional[dict] = None
    """S4 响应体：商科确认前按原文 SWOT 5 / 4P 5 / 盈亏平衡 4 传输。

    `conclusion` 字段保留兼容性；是否从 `dimensions` 拆出综合结论待商科确认。
    """

    framework_type: ReviewFramework
    dimensions: List[ReviewDimension] = []
    conclusion: str
    status: str = "succeeded"
    task_id: Optional[str] = None
    stage: Optional[str] = None
    attempts: int = 0


class TryCaseRequest(Schema):
    """S5 请求体。"""

    text: str
    title: Optional[str] = None
    case_type: CaseType


class TryOption(Schema):
    """试跑选项：与教师端选项同构，按 SPEC 学生端红线**仅此接口**允许下发。

    试跑案例由学生自己贴素材生成，不涉及「猜答案」，因此可以完整下发。
    """

    key: OptionKey
    label: str
    risk_level: RiskLevel
    summary: str
    metrics: Metrics


class TryNode(Schema):
    """试跑节点；试跑不落库，`id` 用生成时的 `idx` 代替。"""

    id: int
    idx: int
    node_role: NodeRole
    title: str
    background: str
    options: List[TryOption] = []


class TryCaseEcho(Schema):
    """S5 / S6 共用的响应体形状。"""

    try_id: str
    case_type: CaseType
    background: str
    base_metrics: Optional[Metrics] = None
    nodes: List[TryNode] = []


class TryCaseResponse(TryCaseEcho):
    """S5 响应体。"""


class TryGetResponse(TryCaseEcho):
    """S6 响应体。"""
