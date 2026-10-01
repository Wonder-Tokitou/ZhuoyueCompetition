"""Build AI inputs from already authorized, frozen platform records."""
from copy import deepcopy
from contracts.ai import TeachingPolicy, ReviewInput
from backend.app.domain import rules
from backend.app.domain.path_comparison import path_comparison

def evidence(case, session, turns):
    snap = session.case_snapshot_json or {}
    nodes = {n["id"]: n for n in snap.get("nodes", [])}
    path = []
    for turn in turns:
        node = nodes.get(turn.node_id, {})
        choice = next((o for o in node.get("options", []) if o["key"] == turn.chosen_option), {})
        path.append({"node_id": turn.node_id, "idx": node.get("idx"), "title": node.get("title"),
                     "strategy": choice.get("label") or '策略文字未记录', "reason": turn.input_text,
                     "before": turn.before_metrics_json, "after": turn.after_metrics_json,
                     "delta": turn.delta_metrics_json, "summary": (turn.result_json or {}).get('summary', '')})
    # Internal option keys and teacher path codes are not student-facing evidence.
    published = deepcopy({key: value for key, value in snap.items() if key not in ('source_text', 'simulation', 'reference_result')})
    for node in published.get('nodes', []):
        for option in node.get('options', []):
            option.pop('key', None)
    return {"original_material": snap.get("source_text", case.source_text),
            "published_case": published, "decisions": path,
            "path_comparison": path_comparison(snap, turns)}



def teaching_policy():
    return TeachingPolicy(**deepcopy({key: getattr(rules, key) for key in TeachingPolicy.__dataclass_fields__}))

def review_input(case, session, turns):
    framework = rules.CASE_TYPE_TO_FRAMEWORK[(session.case_snapshot_json or {}).get("case_type", case.case_type)]
    return ReviewInput(evidence(case, session, turns), framework, list(rules.FRAMEWORK_DIMENSIONS[framework]))
