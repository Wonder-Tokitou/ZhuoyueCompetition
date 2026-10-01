"""8 张表的 SQLAlchemy 模型，字段严格对齐 SPEC.md §1。

注意保留字：表名 `case` 与列名 `idx` 在 SQLite 中是保留字。
SQLAlchemy 生成 SQL 时会自动加引号，但**手写原生 SQL 时必须写成 "case" / "idx"**。
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.app.db import Base


class Framework(Base):
    """商科分析框架（SWOT分析模型 / 4P营销理论 / 盈亏平衡分析）。"""

    __tablename__ = "framework"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    prompt_template = Column(Text, nullable=False)
    output_schema = Column(JSON, nullable=False)
    version = Column(String(32), nullable=False, default="v1")
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    cases = relationship("Case", back_populates="framework")


class Case(Base):
    """教学案例。owner_type=student_ephemeral 的是学生自助试算，不进案例库。"""

    # 保留字表名：ORM 会自动加引号，原生 SQL 需手写 "case"
    __tablename__ = "case"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    source_text = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="draft")  # draft/generating/ready/published
    framework_id = Column(Integer, ForeignKey("framework.id"), nullable=True)
    teacher_token = Column(String(64), nullable=False, unique=True)
    student_token = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    published_at = Column(DateTime, nullable=True)
    # 以下 4 列为 2026-09-22 修订新增
    case_type = Column(String(32), nullable=False)  # 战略决策类/市场营销类/财务管理类
    source_kind = Column(String(16), nullable=False, default="material")  # material/topic
    base_metrics_json = Column(JSON, nullable=True)
    base_net_profit = Column(Float, nullable=True)
    financial_assumptions_json = Column(JSON, nullable=True)
    owner_type = Column(String(24), nullable=False, default="teacher")  # teacher/student_ephemeral
    # 学生端首屏用的两段题面文案；建行时先写空串，生成成功后覆盖
    background = Column(Text, nullable=False)  # 企业背景
    dilemma = Column(Text, nullable=False)  # 核心经营困境
    version = Column(Integer, nullable=False, default=1)
    input_filename = Column(String(255), nullable=True)
    input_mime = Column(String(128), nullable=True)
    input_size = Column(Integer, nullable=True)

    framework = relationship("Framework", back_populates="cases")
    nodes = relationship(
        "Node", back_populates="case", order_by="Node.idx", cascade="all, delete-orphan"
    )
    sessions = relationship("Session", back_populates="case", cascade="all, delete-orphan")
    ai_tasks = relationship("AiTask", back_populates="case")


class SimulationConfig(Base):
    """Teacher-owned rules and the last published snapshot; never written by AI."""
    __tablename__ = "simulation_config"
    case_id = Column(Integer, ForeignKey("case.id"), primary_key=True)
    draft_json = Column(JSON, default=dict)
    published_snapshot_json = Column(JSON, nullable=True)


class Node(Base):
    """推演节点（决策点），每个案例固定 3 个。"""

    __tablename__ = "node"
    __table_args__ = (UniqueConstraint("case_id", "idx", name="uq_node_case_idx"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("case.id"), nullable=False)
    # 保留字列名：原生 SQL 需手写 "idx"
    idx = Column(Integer, nullable=False)
    scenario = Column(Text, nullable=False)
    options_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    # 以下 3 列为 2026-09-22 修订新增
    node_role = Column(String(16), nullable=False)  # 核心战略/核心策略/落地执行
    title = Column(String(200), nullable=False)
    background = Column(Text, nullable=False)

    case = relationship("Case", back_populates="nodes")
    option_results = relationship(
        "OptionResult", back_populates="node", cascade="all, delete-orphan"
    )
    turns = relationship("Turn", back_populates="node")


class OptionResult(Base):
    """选项后果（答案）。学生端在提交该选项之前不得下发。

    本表按 SPEC.md 定义**无 created_at**。
    """

    __tablename__ = "option_result"
    __table_args__ = (
        UniqueConstraint("node_id", "option_key", name="uq_option_node_key"),
        UniqueConstraint("node_id", "risk_level", name="uq_option_node_risk"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("node.id"), nullable=False)
    option_key = Column(String(8), nullable=False)  # A/B/C
    label = Column(String(200), nullable=False)
    metrics_json = Column(JSON, nullable=False)  # revenue/gross_margin/market_share/cash_flow
    net_profit = Column(Float, nullable=True)
    financial_basis_json = Column(JSON, nullable=True)
    risk_level = Column(String(8), nullable=False)  # 保守/稳健/激进
    summary = Column(Text, nullable=False)  # 唯一的结果文案字段

    node = relationship("Node", back_populates="option_results")


class Session(Base):
    """一次学生推演；同一 session 内可有多轮 attempt。"""

    __tablename__ = "session"
    __table_args__ = (UniqueConstraint("case_id", "student_id", name="uq_session_case_student"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("case.id"), nullable=False)
    student_name = Column(String(64), nullable=False)
    student_id = Column(Integer, ForeignKey("student_account.id"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)  # 三个节点走完时写入
    attempt_no = Column(Integer, nullable=False, default=1)
    case_version = Column(Integer, nullable=False, default=1)
    case_snapshot_json = Column(JSON, nullable=False, default=dict)
    current_metrics_json = Column(JSON, nullable=False, default=dict)

    case = relationship("Case", back_populates="sessions")
    turns = relationship("Turn", back_populates="session", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="session", cascade="all, delete-orphan")
    ai_tasks = relationship("AiTask", back_populates="session")


class Turn(Base):
    """一次决策回合。"""

    __tablename__ = "turn"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("session.id"), nullable=False)
    node_id = Column(Integer, ForeignKey("node.id"), nullable=False)
    input_text = Column(Text, nullable=True)
    chosen_option = Column(String(8), nullable=False)  # 自定义决策时记空字符串
    result_json = Column(JSON, nullable=False)  # {metrics, summary}
    duration_ms = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    attempt_no = Column(Integer, nullable=False, default=1)
    before_metrics_json = Column(JSON, nullable=True)
    after_metrics_json = Column(JSON, nullable=True)
    delta_metrics_json = Column(JSON, nullable=True)
    result_source = Column(String(32), nullable=True)

    session = relationship("Session", back_populates="turns")
    node = relationship("Node", back_populates="turns")


class Message(Base):
    """对话消息（AI 助教答疑）。"""

    __tablename__ = "message"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("session.id"), nullable=False)
    role = Column(String(16), nullable=False)  # user/assistant/system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    session = relationship("Session", back_populates="messages")


class Review(Base):
    """复盘报告；同一 (session_id, attempt_no) 只留一条。"""

    __tablename__ = "review"
    __table_args__ = (
        UniqueConstraint("session_id", "attempt_no", name="uq_review_session_attempt"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("session.id"), nullable=False)
    attempt_no = Column(Integer, nullable=False, default=1)
    framework_type = Column(String(32), nullable=False)
    dimensions_json = Column(JSON, nullable=False)  # [{"name":..., "content":...}]
    conclusion = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    session = relationship("Session", back_populates="reviews")


class AiTask(Base):
    """可恢复的 AI 工作流任务。

    任务状态不再依赖进程内字典；服务重启后，queued/running 任务可以被
    worker 重新拾取。input/output JSON 只保存编排元数据，较大的内容通过
    AiArtifact 保存到文件制品目录。
    """

    __tablename__ = "ai_task"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_ai_task_task_id"),
        UniqueConstraint("case_id", "kind", "idempotency_key", name="uq_ai_task_idempotency"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False)
    case_id = Column(Integer, ForeignKey("case.id"), nullable=True)
    session_id = Column(Integer, ForeignKey("session.id"), nullable=True)
    kind = Column(String(32), nullable=False)  # case_generation / student_review / case_fix
    status = Column(String(16), nullable=False, default="queued")
    stage = Column(String(64), nullable=False, default="queued")
    idempotency_key = Column(String(128), nullable=True)
    input_json = Column(JSON, nullable=False, default=dict)
    output_json = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    case = relationship("Case", back_populates="ai_tasks")
    session = relationship("Session", back_populates="ai_tasks")
    steps = relationship("AiTaskStep", back_populates="task", cascade="all, delete-orphan")
    artifacts = relationship("AiArtifact", back_populates="task", cascade="all, delete-orphan")


class AiTaskStep(Base):
    """工作流阶段记录，用于进度展示和审计。"""

    __tablename__ = "ai_task_step"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("ai_task.id"), nullable=False)
    stage = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="running")
    attempt = Column(Integer, nullable=False, default=1)
    detail_json = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)

    task = relationship("AiTask", back_populates="steps")


class AiArtifact(Base):
    """AI 输入、输出和校验报告的文件制品索引。"""

    __tablename__ = "ai_artifact"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("ai_task.id"), nullable=True)
    case_id = Column(Integer, ForeignKey("case.id"), nullable=True)
    session_id = Column(Integer, ForeignKey("session.id"), nullable=True)
    kind = Column(String(64), nullable=False)
    path = Column(Text, nullable=False)
    mime = Column(String(128), nullable=False, default="application/json")
    size = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    task = relationship("AiTask", back_populates="artifacts")


class StudentAccount(Base):
    __tablename__ = "student_account"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), nullable=False, unique=True)
    real_name = Column(String(64), nullable=False)
    password_hash = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="active")
    failed_logins = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class StudentLogin(Base):
    __tablename__ = "student_login"
    token_hash = Column(String(64), primary_key=True)
    student_id = Column(Integer, ForeignKey("student_account.id"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)


class TeacherSession(Base):
    """教师登录令牌。**必须落库**：令牌是跨进程凭证。

    历史上令牌只存在 `routers/teacher.py` 的模块级 set 里，多 worker 部署时
    登录落在 worker A、后续请求轮询到 worker B，B 的集合里没有该令牌 →
    403「令牌无效」→ 前端 `transport.ts` 清 sessionStorage 并跳回登录页，
    表现为「教师端登录成功但立刻被踢出」。落库后所有 worker 共用同一份令牌。
    """

    __tablename__ = "teacher_session"

    token = Column(String(64), primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)
