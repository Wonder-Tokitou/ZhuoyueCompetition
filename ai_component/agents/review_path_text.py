"""Reject option-letter shorthand without confusing AI/ABC business terms."""
import re

def letter_path_errors(candidate):
    texts = [candidate.get('conclusion', '')]
    dims = candidate.get('dimensions')
    if isinstance(dims, list):
        texts += [d.get('content', '') for d in dims if isinstance(d, dict)]
    patterns = [
        r'(?<![A-Za-z])[ABCＡＢＣ](?:\s*(?:→|->|—|－|、|/|-)\s*[ABCＡＢＣ]){1,}(?![A-Za-z])',
        r'(?:选择|选取|选项|选了|均选|学生选|参考选|教师选)\s*[ABCＡＢＣ](?![A-Za-z])',
        r'(?:路径|方案|策略)\s*[：:]?\s*[ABCＡＢＣ]{1,3}(?![A-Za-z])',
    ]
    if any(re.search(pattern, text) for text in texts if isinstance(text,str) for pattern in patterns):
        return ['不得用选项字母代替策略内容。请依据decisions.strategy、path_comparison.student_strategy/reference_strategy逐轮改为策略文字；不能根据教师字母猜测学生编号。保留原有数值和分析。']
    return []
