"""Project an authorized teacher case/node into model evidence."""
from ai_component import suggest_fix as generate_suggestion

async def suggest_fix(case, node, question):
    context = [
        "【案例标题】" + (case.title or ""),
        "【企业背景】" + (case.background or ""),
        "【核心经营困境】" + (case.dilemma or ""),
        "【案例类型】" + case.case_type,
    ]
    if node is not None:
        lines = [
            "【节点序号】第 %d 个（%s）" % (node.idx, node.node_role),
            "【节点标题】" + node.title,
            "【节点背景】" + node.background,
        ]
        for option in sorted(node.option_results, key=lambda option: option.option_key):
            lines.append(
                "【选项%s】风险等级=%s；文案=%s；结果摘要=%s；指标=%s"
                % (option.option_key, option.risk_level, option.label,
                   option.summary, option.metrics_json)
            )
        context.append("\n".join(lines))

    return await generate_suggestion(context, question)
