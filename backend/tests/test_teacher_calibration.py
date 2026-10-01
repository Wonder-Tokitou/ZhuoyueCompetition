from sqlalchemy import select

from backend.app import models
from backend.app.routers import teacher
from backend.tests.test_student_workflow import env
from backend.tests.test_accounts import begin, complete
from backend.tests.teacher_helpers import login_teacher


def _seed_calibratable_case(factory):
    old_metrics = {
        "revenue": 100.0,
        "gross_margin": "40.0%",
        "market_share": "10.0%",
        "cash_flow": "现金流平稳",
    }
    with factory() as db:
        case = db.get(models.Case, 1)
        case.title = "教师标题"
        case.background = "教师人工保存的企业背景"
        case.dilemma = "教师人工保存的经营困境"
        case.base_metrics_json = old_metrics
        case.financial_assumptions_json = {"operating_cost": 50.0, "operating_expense": 20.0}
        for node in case.nodes:
            node.title = f"教师节点 {node.idx}"
            node.background = node.scenario = f"教师节点背景 {node.idx}"
            for option in node.option_results:
                option.metrics_json = {
                    "revenue": 120.0,
                    "gross_margin": "45.0%",
                    "market_share": "12.0%",
                    "cash_flow": "选项现金流改善",
                }
                option.financial_basis_json = {
                    "source": "教师人工校准",
                    "assumptions": {"operating_cost": 60.0, "operating_expense": 24.0},
                    "indicators": {},
                }
                option.net_profit = 36.0
        db.commit()


def test_baseline_edit_recalculates_without_ai_and_preserves_teacher_content(env, monkeypatch):
    client, factory = env
    _seed_calibratable_case(factory)
    scheduled = []
    monkeypatch.setattr(teacher, "schedule_task", lambda task_id: scheduled.append(task_id))
    token = login_teacher(client, monkeypatch)
    response = client.patch("/api/cases/1", params={"token": token}, json={
        "base_metrics": {"revenue": 200, "gross_margin": "50.0%",
            "market_share": "12.0%", "cash_flow": "新基准现金流承压"},
        "financial_assumptions": {"operating_cost": 100, "operating_expense": 40},
    })
    assert response.status_code == 200, response.text
    assert scheduled == []  # Baseline saves never enqueue an AI node-regeneration task.

    with factory() as db:
        case = db.get(models.Case, 1)
        assert case.title == "教师标题"
        assert case.background == "教师人工保存的企业背景"
        assert case.dilemma == "教师人工保存的经营困境"
        assert case.base_metrics_json["cash_flow"] == "新基准现金流承压"
        assert case.base_net_profit == 60
        assert not db.query(models.AiTask).filter(models.AiTask.case_id == case.id).all()
        for node in case.nodes:
            assert node.title == f"教师节点 {node.idx}"
            assert node.background == f"教师节点背景 {node.idx}"
            for option in node.option_results:
                assert option.metrics_json == {
                    "revenue": 220.0,
                    "gross_margin": "55.0%",
                    "market_share": "14.0%",
                    "cash_flow": "选项现金流改善",
                }
                assert option.financial_basis_json["assumptions"] == {
                    "operating_cost": 120.0, "operating_expense": 48.0,
                }
                assert option.net_profit == 52
                assert "定性文本" in option.financial_basis_json["indicators"]["cash_flow"]["method"]


def test_invalid_historical_percentage_does_not_partially_rebase(env, monkeypatch):
    client, factory = env
    _seed_calibratable_case(factory)
    with factory() as db:
        case = db.get(models.Case, 1)
        case.nodes[-1].option_results[-1].metrics_json = {
            **case.nodes[-1].option_results[-1].metrics_json, "gross_margin": "未知"
        }
        before = case.nodes[0].option_results[0].metrics_json.copy()
        db.commit()
    token = login_teacher(client, monkeypatch)
    response = client.patch("/api/cases/1", params={"token": token}, json={
        "base_metrics": {"revenue": 200, "gross_margin": "50.0%",
            "market_share": "12.0%", "cash_flow": "现金流新基准"},
        "financial_assumptions": {"operating_cost": 100, "operating_expense": 40},
    })
    assert response.status_code == 422
    with factory() as db:
        case = db.get(models.Case, 1)
        assert case.base_metrics_json["revenue"] == 100
        assert case.nodes[0].option_results[0].metrics_json == before


def test_teacher_can_delete_case_with_student_submission(env, monkeypatch):
    client, factory = env
    complete(client, begin(client))
    with factory() as db:
        for task in db.scalars(select(models.AiTask).where(models.AiTask.session_id.is_not(None))):
            task.status = "succeeded"
        db.commit()
    token = login_teacher(client, monkeypatch)
    response = client.delete("/api/cases/1", params={"token": token})
    assert response.status_code == 200, response.text
    with factory() as db:
        assert db.get(models.Case, 1) is None
        assert db.scalar(select(models.Session).where(models.Session.case_id == 1)) is None
        assert db.scalar(select(models.Node).where(models.Node.case_id == 1)) is None


def test_manual_node_metric_edit_updates_its_financial_audit_basis(env, monkeypatch):
    client, factory = env
    _seed_calibratable_case(factory)
    with factory() as db:
        node = db.scalar(select(models.Node).where(models.Node.case_id == 1).order_by(models.Node.idx))
        node_id = node.id
        options = []
        for row in node.option_results:
            metrics = dict(row.metrics_json)
            if row.option_key == "A":
                metrics["revenue"] = 130
            options.append({
                "key": row.option_key, "label": row.label, "risk_level": row.risk_level,
                "metrics": metrics, "financial_assumptions": row.financial_basis_json["assumptions"],
                "financial_basis": row.financial_basis_json, "summary": row.summary,
            })
    token = login_teacher(client, monkeypatch)
    response = client.patch(f"/api/cases/1/nodes/{node_id}", params={"token": token},
                            json={"options": options})
    assert response.status_code == 200, response.text
    with factory() as db:
        option = db.scalar(select(models.OptionResult).where(
            models.OptionResult.node_id == node_id, models.OptionResult.option_key == "A"))
        basis = option.financial_basis_json
        assert basis["indicators"]["revenue"]["before"] == 120
        assert basis["indicators"]["revenue"]["after"] == 130
        assert "教师人工校准" in basis["indicators"]["revenue"]["method"]
        assert basis["indicators"]["cash_flow"]["after"] == option.metrics_json["cash_flow"]
        assert option.net_profit == 46
