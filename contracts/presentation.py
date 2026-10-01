"""Readable evidence at the presentation boundary; stored artifacts stay lossless."""
import json
import re

SOURCE_LABELS = {
    'read_case': '案例资料', 'read_node': '当前节点',
    'read_results': '已提交结果', 'read_result': '已提交结果',
    'read_rules': '教学规则',
}


def readable_citations(text):
    """Keep human-readable attribution, not internal tool identifiers."""
    def label(match):
        key = match.group(1).replace('\\_', '_')
        title = SOURCE_LABELS.get(key, '外部资料 ' + key[4:] if key.startswith('web_') else '资料依据')
        return '【' + title + '】'
    return re.sub(r'\[((?:read|web)(?:\\?_)[A-Za-z0-9_\\]+)\]', label, text)

LABELS = {
    'title': '标题', 'background': '背景', 'dilemma': '经营困境',
    'base_metrics': '基准指标', 'revenue': '营收', 'gross_margin': '毛利率',
    'market_share': '市场份额', 'cash_flow': '现金流', 'node': '节点',
    'strategy': '所选策略', 'strategies': '可选策略', 'reason': '选择理由',
    'before': '决策前', 'after': '决策后', 'summary': '结果摘要',
    'framework': '分析框架', 'transmission': '经营传导规则', 'units': '单位',
    'decision_type': '决策类型',
}


def readable_evidence(value, depth=0):
    indent = '  ' * depth
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = LABELS.get(key, '资料')
            if isinstance(item, (dict, list)):
                lines.append(f'{indent}{label}：\n{readable_evidence(item, depth + 1)}')
            else:
                text = '未提供' if item is None or item == '' else str(item)
                if key == 'revenue' and isinstance(item, (int, float)):
                    text = f'{item:g} 万元'
                lines.append(f'{indent}{label}：{text}')
        return '\n'.join(lines)
    if isinstance(value, list):
        return '\n\n'.join(readable_evidence(item, depth) for item in value) or indent + '暂无已提交记录'
    return indent + str(value or '未提供')


def readable_tutor_history(text):
    """Only convert JSON lines in the legacy system-generated source appendix.

    No DB rewrite; called only for assistant messages. Source labels are translated.
    """
    head, sep, appendix = text.partition('\n\n依据：\n')
    if not sep:
        return readable_citations(text)
    lines = []
    for line in appendix.splitlines():
        if line.lstrip().startswith(('{', '[')):
            try:
                value = json.loads(line)
                if isinstance(value, (dict, list)):
                    line = readable_evidence(value)
            except (ValueError, TypeError):
                pass
        lines.append(line)
    return readable_citations(head + sep + '\n'.join(lines))
