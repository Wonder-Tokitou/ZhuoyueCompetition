"""Validate completeness without changing the model's dimension count or order."""
import json
import re
import unicodedata



def required_dimensions(dimensions):
    return [name for name in dimensions if name != '综合结论']


def output_contract(dimensions):
    return ('dimensions为动态数组，不限制总项数或排列顺序；必须完整覆盖基础分析内容：'
            + json.dumps(required_dimensions(dimensions), ensure_ascii=False)
            + '。可以增加有依据的补充分析项，各项名称和正文非空，不重复或混入其他框架。'
            '综合结论放在独立conclusion字段，无需在dimensions重复，也不要为凑项数补项。'
            '只返回JSON {"dimensions":[{"name":"分析项名称","content":"结合本次决策的正文"}],'
            '"conclusion":"整体评价与可执行改进"}。')


def _key(name):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', name)).casefold()


def validate_review(candidate, dimensions):
    """Read-only validation. Recognized short names are matched, never rewritten."""
    expected = required_dimensions(dimensions)
    aliases = {}
    for name in dimensions:
        key = _key(name)
        aliases[key] = name
        if '(' in key:
            short, _, english = key.partition('(')
            aliases[short] = name
            aliases[english.rstrip(')')] = name
    issues, seen = [], set()
    dims = candidate.get('dimensions')
    conclusion = candidate.get('conclusion')
    if not isinstance(dims, list) or not dims:
        issues.append('分析项必须是非空数组')
    else:
        for index, dimension in enumerate(dims, 1):
            if not isinstance(dimension, dict):
                issues.append(f'第{index}项必须含名称和正文')
                continue
            name, content = dimension.get('name'), dimension.get('content')
            if not isinstance(name, str) or not name.strip():
                issues.append(f'第{index}项名称不能为空')
            else:
                key = aliases.get(_key(name), _key(name))
                if key in seen:
                    issues.append(f'分析项重复：{name}；请合并重复分析，保留其他有效内容')
                seen.add(key)
            if not isinstance(content, str) or not content.strip():
                issues.append(f'第{index}项正文不能为空')
    missing = [name for name in expected if name not in seen]
    if missing:
        issues.append('缺少基础分析内容：' + '、'.join(missing) + '；不要求固定总项数或顺序')
    if not isinstance(conclusion, str) or not conclusion.strip():
        issues.append('综合结论不能为空')
    return issues
