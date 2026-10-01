"""Teacher rule persistence and publication boundary, separate from AI workflows."""
from copy import deepcopy
from sqlalchemy.orm import object_session
from backend.app import models
from backend.app.domain import progressive


def live_snapshot(case):
    return {"source_text": case.source_text, "version": case.version,
        "title": case.title, "case_type": case.case_type, "background": case.background,
        "dilemma": case.dilemma, "base_metrics": deepcopy(case.base_metrics_json or {}),
        "nodes": [{"id": n.id, "idx": n.idx, "node_role": n.node_role, "title": n.title,
            "background": n.background, "options": [{"key": o.option_key, "label": o.label,
                "risk_level": o.risk_level, "summary": o.summary, "metrics": deepcopy(o.metrics_json)}
                for o in sorted(n.option_results, key=lambda o:o.option_key)]}
            for n in sorted(case.nodes, key=lambda n:n.idx)]}


def published_snapshot(case):
    db = object_session(case)
    config = db.get(models.SimulationConfig, case.id) if db else None
    return deepcopy(config.published_snapshot_json) if config and config.published_snapshot_json else live_snapshot(case)


def prepare(case, config):
    snapshot = live_snapshot(case)
    result = progressive.preview(snapshot, config, case.financial_assumptions_json or {})
    snapshot["base_metrics"] = result["baseline"]
    snapshot["simulation"] = {"version": 1, **deepcopy(config)}
    reference = config.get('reference_path')
    if reference:
        selected = next(p for p in result['paths'] if p['path'] == reference['path'])
        snapshot['reference_result'] = {**deepcopy(selected), 'kind': reference['kind'],
            'strategies': [next(o['label'] for o in n['options'] if o['key'] == key)
                for n, key in zip(sorted(snapshot['nodes'], key=lambda n:n['idx']), reference['path'].split('→'))]}
    # Do not expose obsolete absolute outcomes to the tutor/reviewer as evidence.
    for node in snapshot["nodes"]:
        for option in node["options"]:
            option.pop("metrics", None)
            option["summary"] = "结果由上一轮财务状态与教师确认的影响规则计算，以学生实际决策记录为准。"
    return snapshot, result
