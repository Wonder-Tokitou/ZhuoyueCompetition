"""Completed-attempt comparison using only the session's frozen rules."""
from backend.app.domain.progressive import apply_impact

def path_comparison(snapshot, turns):
    config = snapshot.get('simulation') or {}
    reference = config.get('reference_path')
    if not config.get('enabled') or not reference:
        return None
    nodes = sorted(snapshot.get('nodes', []), key=lambda n:n['idx'])
    by_node = {t.node_id:t for t in turns}
    if len(nodes) != 3 or len(turns) != 3 or any(n['id'] not in by_node for n in nodes):
        return None
    state = dict(snapshot['base_metrics'])
    rows = []
    for node, key in zip(nodes, reference['path'].split('→')):
        turn = by_node[node['id']]
        option = next(o for o in node['options'] if o['key']==key)
        chosen = next((o for o in node['options'] if o['key']==turn.chosen_option), {})
        frozen = snapshot.get('reference_result')
        state = dict(frozen['steps'][len(rows)]) if frozen and frozen.get('path') == reference['path'] else apply_impact(state, config['rules'][f'{node["id"]}:{key}'])
        actual = turn.after_metrics_json or {}
        rows.append({'round':node['idx'], 'student_strategy':chosen.get('label') or '策略文字未记录',
            'reference_strategy':option['label'], 'same':turn.chosen_option==key,
            'student_metrics':actual, 'reference_metrics':dict(state),
            'difference': {k:round(float(actual[k])-float(state[k]),2) for k in ['revenue','operating_cost','operating_expense','net_profit','cash_flow_amount'] if k in actual}})
    return {'kind':reference['kind'], 'rounds':rows,
        'note':'参考路径不代表唯一正确答案。两条路径均使用本次推演冻结的教学规则；参考结果是模拟计算，并非企业真实财报。差额为你的结果减参考结果。'}
