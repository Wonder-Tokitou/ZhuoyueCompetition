"""Session-scoped, read-only capabilities and tutor artifact persistence."""
import re
from sqlalchemy import select
from backend.app import models
from backend.app.domain import rules
from backend.app.domain.student_labels import student_label as _student_label
from backend.app.observability import event
from contracts.errors import LLMFailed
from contracts.tutor import TOOLS
from contracts.presentation import readable_evidence, readable_citations
from ai_component import TutorAgent
from .artifacts import artifact_store
from .web_search import search_business

def save_case_file(db, session):
    return artifact_store.save_json(db, session.case_snapshot_json, kind="session_case_snapshot",
                                   case_id=session.case_id, session_id=session.id)


def execute_tools(db, session, names):
    """工具不接受模型传来的路径、SQL、案例ID或会话ID，只读当前会话文件。"""
    artifact = db.scalar(select(models.AiArtifact).where(models.AiArtifact.session_id == session.id,
        models.AiArtifact.kind == "session_case_snapshot").order_by(models.AiArtifact.id.desc()))
    if artifact is None:  # 兼容旧会话，首次咨询归档已冻结的快照。
        artifact = save_case_file(db, session)
        db.commit()
    snap = artifact_store.read_json(artifact)
    turns = sorted([t for t in session.turns if t.attempt_no == session.attempt_no], key=lambda t: t.id)
    node = next((n for n in snap["nodes"] if n["idx"] == min(len(turns) + 1, 3)), {})
    results = []
    for turn in turns:
        n = next((n for n in snap["nodes"] if n["id"] == turn.node_id), {})
        option = next((o for o in n.get("options", []) if o["key"] == turn.chosen_option), {})
        results.append({"node": n.get("idx"), "strategy": _student_label(option.get("label", ""), ""),
                        "reason": turn.input_text, "before": turn.before_metrics_json,
                        "after": turn.after_metrics_json, "summary": turn.result_json.get("summary")})
    views = {
        "read_case": {k: snap.get(k) for k in ("title", "background", "dilemma", "base_metrics")},
        "read_node": {"title": node.get("title"), "background": node.get("background"),
                      "strategies": [_student_label(o["label"], "") for o in node.get("options", [])]},
        "read_results": results,
        "read_rules": {"framework": rules.CASE_TYPE_TO_FRAMEWORK[snap["case_type"]],
                       "transmission": rules.TRANSMISSION_RULES, "units": "金额万元，比例一位小数，现金流为方向描述"},
    }
    if snap.get("simulation", {}).get("enabled"):
        views["read_rules"]["units"] = (
            "递进推演：金额为万元且期间/企业范围一致；毛利率和份额变化用百分点。"
            "营收、费用、现金净流量按上一轮金额乘(1+变化率/100)。"
            "成本=营收×(1−毛利率/100)，净利润=营收−成本−费用。"
            "现金净流量为有符号金额，负数代表流出，不是账户余额；只解释已提交结果，不猜测未选分支。"
        )
        views["read_rules"]["submitted_impacts"] = [
            {"node_id": t.node_id, "impact": snap["simulation"]["rules"].get(f'{t.node_id}:{t.chosen_option}')}
            for t in turns
        ]
    sources = []
    for name in dict.fromkeys(names):
        if name not in TOOLS:
            raise LLMFailed("答疑请求了未授权工具")
        source = {"id": name, "title": TOOLS[name], "version": session.case_version, "content": views[name]}
        sources.append(source)
        event("答疑Agent：只读工具执行", tool=name, session_id=session.id, artifact_id=artifact.id)
    return sources



class SessionTutorTools:
    def __init__(self, db, session):
        self.db, self.session = db, session

    def read(self, names):
        return execute_tools(self.db, self.session, names)

    async def search(self, query):
        session, db = self.session, self.db
        account = db.get(models.StudentAccount, session.student_id) if session.student_id else None
        private = [session.student_name, account.username if account else ""]
        if any(value and value in query for value in private) or re.search(r"https?://|@|[/\\]|\d{4,}", query):
            return [], "搜索词含可能的私人信息，已跳过外部搜索。"
        return await search_business(query)

    def record(self, data):
        artifact_store.save_json(self.db, data, kind="tutor_answer",
                                 case_id=self.session.case_id, session_id=self.session.id)
        self.db.commit()

async def answer_question(db, session, question):
    return await TutorAgent().run(question, SessionTutorTools(db, session))
