# -*- coding: utf-8 -*-
"""从 docs/source/ 的两份商科 docx 重新生成 docs/ 下的交付文件。

由本脚本产出：
    docs/商科规则.md     正文与表格逐字转录（含两份文档全部 16 张表）
    docs/商科规则.json   规则结构：case_types / industry_baseline / transmission_rules /
                         review_templates / metric_formulas / common_constraints /
                         input_formats / material_requirements
    docs/标杆案例.json   研咖咖啡案例：原文层（source_*）+ 标准化层（simulation_* / standard_*）

设计要点：
  - 只依赖 Python 标准库解析 DOCX，不引入其他第三方库。
  - 商科规则.md 与两份 JSON 里的「原文层」全部由 docx 机械转录，不手抄。
  - 落盘前逐条断言：原文层的每个字符串值必须「逐字出现在 商科规则.md 中」；
    标准化层的每个数值必须能由原文单元格解析得到。任一处不命中即报错退出。
  - **推导值必须显式标注**（source_status / metric_source），不得伪装成商科原文。

用法（在仓库任意位置执行均可）：
    python docs/build_docs.py
"""
import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "source"
OUT = ROOT / "docs"

DOC_RULES = SRC / "AI商科案例交互式推演系统-商科侧逻辑框架与指令规范.docx"
DOC_CASE = SRC / "标杆案例-研咖咖啡·高校市场扩张与定价决策（技术调试标准版）.docx"


# ─────────────────────────────── docx 解析 ───────────────────────────────

class _Cell:
    def __init__(self, text):
        self.text = text


class _Row:
    def __init__(self, cells):
        self.cells = [_Cell(text) for text in cells]


class _Table:
    def __init__(self, rows):
        self.rows = [_Row(row) for row in rows]


def _node_text(node):
    """提取 DOCX 节点内所有文字，保持文档中的文字顺序。"""
    return ''.join(item.text or '' for item in node.iter(W + 't'))


def _docx_parts(path):
    """用标准库读取 document.xml 与 styles.xml，避免运行时依赖 python-docx。"""
    with ZipFile(path) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        styles = ET.fromstring(archive.read('word/styles.xml'))

    style_names = {}
    for style in styles.findall('.//' + W + 'style'):
        style_id = style.attrib.get(W + 'styleId')
        name = style.find(W + 'name')
        if style_id and name is not None:
            style_names[style_id] = name.attrib.get(W + 'val', '')
    body = document.find(W + 'body')
    if body is None:
        raise RuntimeError('DOCX 缺少 word/document.xml 的 body：%s' % path)
    return body, style_names


def iter_blocks(path):
    """按 body 真实顺序产出 ('h', 级别, 文本) / ('p', None, 文本) / ('t', None, 表格)。"""
    body, style_names = _docx_parts(path)
    for child in list(body):
        if child.tag == W + 'p':
            text = _node_text(child).strip()
            if not text:
                continue
            style_node = child.find(W + 'pPr/' + W + 'pStyle')
            style_id = style_node.attrib.get(W + 'val', '') if style_node is not None else ''
            style = style_names.get(style_id, '').lower()
            if style in ('heading 1', '标题 1') or style_id.lower() == 'heading1':
                yield ('h', '1', text)
            elif style in ('heading 2', '标题 2') or style_id.lower() == 'heading2':
                yield ('h', '2', text)
            else:
                yield ('p', None, text)
        elif child.tag == W + 'tbl':
            rows = []
            for tr in child.findall(W + 'tr'):
                rows.append([_node_text(tc) for tc in tr.findall(W + 'tc')])
            yield ('t', None, _Table(rows))


def norm(s):
    """折叠所有空白，便于比对与单元格清理。"""
    return ' '.join(s.split())


def table_to_md(tbl):
    """把 docx 表格转成 Markdown 表格（首行作表头）。"""
    rows = []
    for r in tbl.rows:
        rows.append([norm(c.text).replace('|', '\\|') for c in r.cells])
    width = max(len(r) for r in rows)
    out = []
    for i, r in enumerate(rows):
        r = r + [''] * (width - len(r))
        out.append('| ' + ' | '.join(r) + ' |')
        if i == 0:
            out.append('|' + ' --- |' * width)
    return '\n'.join(out)


def transcribe(path, part_title):
    """转录一份 docx。跳过「目录」区：属导航内容而非正文，且页码对 Markdown 无意义。"""
    lines = ['# %s' % part_title, '']
    skipping_toc = False
    for kind, lvl, payload in iter_blocks(path):
        if kind == 'p':
            # 「目  录」标题用的是全角/连续空格，先去空白再比对
            if norm(payload).replace(' ', '') == '目录':
                skipping_toc = True
                continue
            if skipping_toc:
                continue
            lines += [payload, '']
        elif kind == 'h':
            skipping_toc = False
            lines += ['%s %s' % ('##' if lvl == '1' else '###', payload), '']
        else:
            lines += [table_to_md(payload), '']
    return '\n'.join(lines).rstrip() + '\n'


# ─────────────────────────── docs/商科规则.json ───────────────────────────

RULES = {
    "case_types": [
        {"case_type": "战略决策类", "framework": "SWOT分析模型",
         "core_dimensions": "优势、劣势、机会、威胁"},
        {"case_type": "市场营销类", "framework": "4P营销理论",
         "core_dimensions": "产品、价格、渠道、促销"},
        {"case_type": "财务管理类", "framework": "盈亏平衡分析",
         "core_dimensions": "成本、销量、单价、利润"},
    ],
    "industry_baseline": [
        {"industry": "校园餐饮/零售", "gross_margin_range": "55%—70%",
         "revenue_scale": "单店月营收8万—20万元", "expense_ratio": "市场费用占营收8%—15%"},
        {"industry": "制造类行业", "gross_margin_range": "20%—35%",
         "revenue_scale": "年增速10%—25%", "expense_ratio": "研发费用占营收3%—8%"},
    ],
    "transmission_rules": [
        {"decision_type": "高价策略", "revenue": "短期下降，长期平稳",
         "gross_margin": "上升", "market_share": "小幅下降", "cash_flow": "短期收紧"},
        {"decision_type": "低价策略", "revenue": "短期上升",
         "gross_margin": "下降", "market_share": "上升", "cash_flow": "短期流出增加"},
        {"decision_type": "渠道扩张", "revenue": "中期上升",
         "gross_margin": "小幅下降", "market_share": "上升", "cash_flow": "短期大幅流出"},
        {"decision_type": "加大营销投入", "revenue": "短期上升",
         "gross_margin": "小幅下降", "market_share": "上升", "cash_flow": "短期流出增加"},
        {"decision_type": "成本控制", "revenue": "影响小",
         "gross_margin": "上升", "market_share": "影响小", "cash_flow": "改善"},
    ],
    "review_templates": [
        # 商科组确认前严格保留原文 5/5/4；是否把「综合结论」从 dimensions
        # 拆出，待确认后再决定。当前 dimensions 先按原文完整传输。
         {"template": "SWOT分析模型", "conclusion_name": "综合结论",
          "dimensions": ["优势（S）", "劣势（W）", "机会（O）", "威胁（T）", "综合结论"]},
         {"template": "4P营销理论", "conclusion_name": "综合结论",
          "dimensions": ["产品（Product）", "价格（Price）", "渠道（Place）", "促销（Promotion）", "综合结论"]},
         {"template": "盈亏平衡分析", "conclusion_name": "综合结论",
          "dimensions": ["成本端", "销量端", "盈利端", "综合结论"]},
    ],
    # ── 以下四组为 2026-09-23 新增（值均逐字取自 商科规则.md） ──
    "metric_formulas": [
        {"metric": "毛利率", "formula": "（营收－营业成本）÷营收",
         "note": "衡量单位收入的盈利水平", "unit": "百分比，保留一位小数"},
        {"metric": "净利润", "formula": "营收－营业成本－运营费用",
         "note": "综合经营成果", "unit": "万元"},
        {"metric": "市场份额", "formula": "企业营收÷目标市场总规模",
         "note": "衡量市场地位", "unit": "百分比，保留一位小数"},
    ],
    # 原文 §5.3 的公式表里没有「单位」这一列；上面的 unit 是按 §3.4 的口径拼装出来的，
    # 因此单独记一条依据，避免把技术拼装当成原文。
    "metric_formulas_unit_basis": (
        "unit 取自商科规范 §3.4「百分比保留一位小数」「金额单位统一为万元」；"
        "原文公式表内未标注单位（技术侧拼装，已在 商科待确认项.md 登记）"
    ),
    "common_constraints": [
        {"key": "language", "rule": "系统全部内容默认使用简体中文输出", "source": "商科规范 §3.4"},
        {"key": "number_format", "rule": "数字一律使用阿拉伯数字", "source": "商科规范 §3.4"},
        {"key": "amount_unit", "rule": "金额单位统一为万元", "source": "商科规范 §3.4"},
        {"key": "percentage_format", "rule": "百分比保留一位小数", "source": "商科规范 §3.4"},
        {"key": "terminology", "rule": "统一使用标准商科术语，禁止口语化、网络化表述",
         "source": "商科规范 §3.2"},
        {"key": "no_fabrication",
         "rule": "所有分析与推演仅围绕案例给定的企业背景与行业环境展开，禁止无依据的发散与脑补；"
                 "AI不得自行引入案例未提及的竞争对手、政策或数据",
         "source": "商科规范 §3.3"},
    ],
    "input_formats": [
        {"format": "Word", "rule": "完整的教学案例文档（Word、PDF）", "source": "商科规范 §4.1"},
        {"format": "PDF", "rule": "完整的教学案例文档（Word、PDF）", "source": "商科规范 §4.1"},
        {"format": "财报片段", "rule": "企业财报片段加经营事件描述", "source": "商科规范 §4.1"},
        {"format": "新闻与行业分析文章", "rule": "商业新闻与行业分析文章", "source": "商科规范 §4.1"},
        {"format": "主题关键词", "rule": "极简主题关键词（如校园咖啡店定价决策）",
         "source": "商科规范 §4.1"},
        {"format": "纯文本", "rule": "纯文本素材（系统实现对齐：T2 接受 .txt/.md 直读）",
         "source": "系统实现，非商科原文"},
    ],
    "material_requirements": [
        {"key": "enterprise_background", "rule": "企业基本背景，即所属行业、核心业务、当前经营现状",
         "source": "商科规范 §4.1"},
        {"key": "core_decision", "rule": "核心决策问题，即企业面临的具体选择困境",
         "source": "商科规范 §4.1"},
        {"key": "baseline_data", "rule": "基础经营基准数据，即当前营收、成本、市场份额等",
         "source": "商科规范 §4.1"},
    ],
}

# ─────────────────────────── docs/标杆案例.json ───────────────────────────

NODES = [
    {"node_role": "核心战略", "title": "市场扩张策略",
     "background": "是深耕现有市场提升单店盈利，还是加快扩张抢占其他高校市场",
     "options": [
         {"option_key": "A", "risk_level": "保守",
          "label": "深耕现有校区，暂不扩张，聚焦提升单店复购率与运营效率"},
         {"option_key": "B", "risk_level": "稳健",
          "label": "拓展1所新高校，开设2家新店，稳步扩大品牌覆盖"},
         {"option_key": "C", "risk_level": "激进",
          "label": "半年内同时进入3所新高校，开设5家新店，快速抢占市场"},
     ]},
    {"node_role": "核心策略", "title": "定价与产品策略",
     "background": "是维持平价定位，还是通过产品升级与价格调整提升品牌价值",
     "options": [
         {"option_key": "A", "risk_level": "保守",
          "label": "维持现有定价体系（美式12元/杯），小幅优化产品与控制成本"},
         {"option_key": "B", "risk_level": "稳健",
          "label": "推出12—18元中端新品系列，升级核心产品品质，保持价格竞争力"},
         {"option_key": "C", "risk_level": "激进",
          "label": "全线提价10%—15%，定位高端精品咖啡，强化品牌溢价"},
     ]},
    {"node_role": "落地执行", "title": "营销推广策略",
     "background": "营销投入应控制在什么水平才能实现最优的投入产出",
     "options": [
         {"option_key": "A", "risk_level": "保守",
          "label": "以口碑营销为主，依靠学生自发传播与会员复购，营销预算占营收5%"},
         {"option_key": "B", "risk_level": "稳健",
          "label": "校园活动赞助、社交媒体种草与会员优惠组合推广，营销预算占营收10%"},
         {"option_key": "C", "risk_level": "激进",
          "label": "大规模广告投放与高频促销补贴，营销预算占营收20%"},
     ]},
]

# 9 组财务结果：取各节点结果表的 4 个数值列（现金流表现/说明等定性列保留在 商科规则.md）
RESULTS = [
    (1, "A", {"store_count": 5, "monthly_revenue": 13,
              "gross_margin": "63.0%", "market_share": "20.0%"}),
    (1, "B", {"store_count": 7, "monthly_revenue": 12.6,
              "gross_margin": "62.5%", "market_share": "26.0%"}),
    (1, "C", {"store_count": 10, "monthly_revenue": 10.5,
              "gross_margin": "60.0%", "market_share": "35.0%"}),
    (2, "A", {"avg_price": "12元", "monthly_revenue": 12,
              "gross_margin": "62.0%", "market_share": "20.0%"}),
    (2, "B", {"avg_price": "13.5元", "monthly_revenue": 14,
              "gross_margin": "64.0%", "market_share": "22.0%"}),
    (2, "C", {"avg_price": "14元", "monthly_revenue": 11,
              "gross_margin": "68.0%", "market_share": "19.0%"}),
    (3, "A", {"marketing_expense_ratio": "5.0%", "monthly_revenue": 12.5,
              "gross_margin": "62.0%", "market_share": "20.0%"}),
    (3, "B", {"marketing_expense_ratio": "10.0%", "monthly_revenue": 14.5,
              "gross_margin": "63.5%", "market_share": "22.0%"}),
    (3, "C", {"marketing_expense_ratio": "20.0%", "monthly_revenue": 15,
              "gross_margin": "61.0%", "market_share": "24.0%"}),
]

def collect_tables(path):
    """返回 {表号: {"caption", "header", "rows"}}，单元格内容逐字保留。"""
    tables = {}
    pending = ""
    for kind, _lvl, payload in iter_blocks(path):
        if kind == 'p':
            text = norm(payload)
            if re.match(r'^表\d+', text):
                pending = text
        elif kind == 'h':
            pending = ""
        elif kind == 't':
            rows = [[norm(c.text) for c in r.cells] for r in payload.rows]
            if not rows:
                continue
            matched = re.match(r'^表(\d+)', pending)
            key = matched.group(1) if matched else '#%d' % (len(tables) + 1)
            tables[key] = {"caption": pending or "(无表题)", "header": rows[0], "rows": rows[1:]}
            pending = ""
    return tables


def collect_section_paragraphs(path, heading_text):
    """取指定标题下的正文段落（不含标题本身），遇到下一个标题即停。"""
    out, capture = [], False
    for kind, _lvl, payload in iter_blocks(path):
        if kind == 'h':
            if capture:
                break
            capture = (norm(payload) == heading_text)
            continue
        if capture and kind == 'p':
            out.append(payload)
    return out


# ───────────────── 标杆案例：原文层（逐字，全部机械提取） ─────────────────

CASE_TABLES = collect_tables(DOC_CASE)


def _table(number):
    if number not in CASE_TABLES:
        raise RuntimeError("benchmark docx 缺少 表%s" % number)
    return CASE_TABLES[number]


#: 表2 案例初始基准数据（原文）
SOURCE_BASE_METRICS = _table("2")
#: 表6 / 表7 / 表8 各决策节点财务结果测算（原文）
SOURCE_NODE_RESULTS = [_table("6"), _table("7"), _table("8")]
#: 表9 标准决策路径最终结果（原文）
SOURCE_STANDARD_PATH_RESULT = _table("9")

#: 企业背景：§2 的两段正文，逐字取自 docx
SAMPLE_BACKGROUND = "\n".join(collect_section_paragraphs(DOC_CASE, "2 企业背景"))
#: 核心经营困境：§3 的两段正文，逐字取自 docx
SAMPLE_DILEMMA = "\n".join(collect_section_paragraphs(DOC_CASE, "3 核心经营困境"))
#: §7.1 三条选择理由，逐字取自 docx
STANDARD_PATH_REASON_TEXTS = collect_section_paragraphs(DOC_CASE, "7.1 选择理由")


def _standard_path() -> str:
    """从 §7 正文里机械解析出 B-B-B，不手写。"""
    text = "\n".join(collect_section_paragraphs(DOC_CASE, "7 标准决策路径（标杆答案）"))
    letters = re.findall(r"决策节点[一二三]选([ABC])", text)
    if len(letters) != 3:
        raise RuntimeError("无法从 §7 正文解析出三个决策节点选项：%r" % text)
    return "-".join(letters)


def _standard_review():
    """§8 的标准复盘：按原文 5 个维度保存，conclusion 仅作兼容字段。"""
    paragraphs = collect_section_paragraphs(DOC_CASE, "8 标准复盘报告（基于4P营销理论）")
    items = []
    for text in paragraphs:
        matched = re.match(r"^（\d+）([^：]+)：(.*)$", text.strip())
        if matched:
            items.append({"name": matched.group(1).strip(), "content": matched.group(2).strip()})
    if len(items) != 5:
        raise RuntimeError("§8 标准复盘应解析出 5 项，实际 %d 项" % len(items))
    return {
        "framework_type": "4P营销理论",
        # 确认前不拆分：原文五项全部进入 dimensions。
        "dimensions": items,
        "conclusion": items[-1]["content"],
        "source": "docs/source/标杆案例…docx §8 标准复盘报告（原文五项）",
        "verbatim": True,
        "conclusion_role": "compatibility_alias_of_dimensions_last_item",
    }


STANDARD_REVIEW = _standard_review()

#: 原文给了现金流表现的选项（表6 有「现金流表现」列）
CASH_FLOW_ORIGINAL = {
    (1, "A"): "稳健改善",
    (1, "B"): "短期流出（开店投入约30万元）",
    (1, "C"): "大幅流出（开店投入约75万元）",
}

#: 原文未给现金流的选项：按 transmission_rules 的对应决策类型推导文字方向
#: （表7 / 表8 没有现金流列，全部属技术侧推导）
CASH_FLOW_DERIVATION = {
    (2, "A"): "成本控制",
    (2, "B"): "高价策略",
    (2, "C"): "高价策略",
    (3, "A"): "加大营销投入",
    (3, "B"): "加大营销投入",
    (3, "C"): "加大营销投入",
}

#: 标准化层已核对过的三个指标（值由原文单元格解析得到，见 main() 里的断言）
STANDARDIZED = [
    (1, "A", 13.0, "63.0%", "20.0%"),
    (1, "B", 12.6, "62.5%", "26.0%"),
    (1, "C", 10.5, "60.0%", "35.0%"),
    (2, "A", 12.0, "62.0%", "20.0%"),
    (2, "B", 14.0, "64.0%", "22.0%"),
    (2, "C", 11.0, "68.0%", "19.0%"),
    (3, "A", 12.5, "62.0%", "20.0%"),
    (3, "B", 14.5, "63.5%", "22.0%"),
    (3, "C", 15.0, "61.0%", "24.0%"),
]

#: 原文「说明」列有内容的选项（表7 / 表8）；表6 没有说明列，摘要由技术侧撰写
SUMMARY_ORIGINAL = {
    (2, "A"): "经营平稳，增长有限",
    (2, "B"): "价格带上移，销量稳中有升",
    (2, "C"): "销量下降约18%，部分价格敏感客群流失",
    (3, "A"): "利润率较高，增长缓慢",
    (3, "B"): "投入产出比良好，品牌认知提升",
    (3, "C"): "营收冲高但费用侵蚀利润，存在亏损风险",
}

#: 表6 无说明列，这三条摘要由技术侧按指标撰写（前缀已显式标注）
SUMMARY_DERIVED = {
    (1, "A"): "技术侧摘要：门店数不变，单店月营收 13 万元、毛利率 63%，现金流稳健改善。",
    (1, "B"): "技术侧摘要：门店增至 7 家，单店月营收 12.6 万元、毛利率 62.5%，开店投入约 30 万元。",
    (1, "C"): "技术侧摘要：门店增至 10 家，单店月营收 10.5 万元、毛利率 60%，开店投入约 75 万元。",
}

#: 标准路径（B-B-B）最终状态的标准化指标；表9 没有现金流列，现金流由技术侧推导
STANDARD_FINAL_METRICS = {
    "metrics": {"revenue": 14.5, "gross_margin": "64.0%", "market_share": "28.0%",
                "cash_flow": "短期大幅流出"},
    "metric_source": {"revenue": "original", "gross_margin": "original",
                      "market_share": "original",
                      "cash_flow": "derived_from_transmission_rule"},
    "source_status": "derived_from_transmission_rule",
    "cash_flow_derivation": "渠道扩张（标准路径第 1 节点 B，开店投入是最大的一笔现金流出）",
    "extra_metrics": {"store_count": 7, "avg_price": "13.5元",
                      "marketing_expense_ratio": "10.0%", "monthly_net_profit": 3.3},
}


def _transmission_cash_flow(decision_type: str) -> str:
    for item in RULES["transmission_rules"]:
        if item["decision_type"] == decision_type:
            return item["cash_flow"]
    raise RuntimeError("transmission_rules 里没有决策类型：%s" % decision_type)


def build_simulation_results():
    """拼出 9 条 simulation_results：label 取自 NODES，指标值与原文单元格已核对。"""
    labels = {
        (node_index, option["option_key"]): option["label"]
        for node_index, node in enumerate(NODES, start=1)
        for option in node["options"]
    }
    risks = {
        (node_index, option["option_key"]): option["risk_level"]
        for node_index, node in enumerate(NODES, start=1)
        for option in node["options"]
    }

    results = []
    for node_index, key, revenue, margin, share in STANDARDIZED:
        ident = (node_index, key)
        if ident in CASH_FLOW_ORIGINAL:
            cash_flow = CASH_FLOW_ORIGINAL[ident]
            cash_source = "original"
        else:
            cash_flow = _transmission_cash_flow(CASH_FLOW_DERIVATION[ident])
            cash_source = "derived_from_transmission_rule"

        metric_source = {
            "revenue": "original",
            "gross_margin": "original",
            "market_share": "original",
            "cash_flow": cash_source,
        }
        results.append({
            "node_index": node_index,
            "option_key": key,
            "risk_level": risks[ident],
            "label": labels[ident],
            "summary": SUMMARY_ORIGINAL.get(ident) or SUMMARY_DERIVED[ident],
            "summary_source": "original" if ident in SUMMARY_ORIGINAL
                              else "derived_by_technical_side",
            "metrics": {"revenue": revenue, "gross_margin": margin,
                        "market_share": share, "cash_flow": cash_flow},
            "metric_source": metric_source,
            # 记录级汇总：四个指标全为原文才算 original
            "source_status": "original" if cash_source == "original"
                             else "derived_from_transmission_rule",
        })
    return results


SIMULATION_RESULTS = build_simulation_results()

STANDARD_PATH_REASONS = [
    {"node_index": index, "option_key": letter, "reason": text}
    for index, (letter, text) in enumerate(
        zip(_standard_path().split("-"), STANDARD_PATH_REASON_TEXTS), start=1
    )
]

CASE = {
    "title": "研咖咖啡·高校市场扩张与定价决策",
    "case_type": "市场营销类",
    "framework": "4P营销理论",
    # ── 原文层（逐字取自 docx） ──
    "background": SAMPLE_BACKGROUND,
    "dilemma": SAMPLE_DILEMMA,
    "source_results": {
        "base_metrics": SOURCE_BASE_METRICS,
        "node_results": SOURCE_NODE_RESULTS,
        "standard_path_result": SOURCE_STANDARD_PATH_RESULT,
    },
    # ── 兼容层（步骤 4 已在用，保留不动） ──
    "base_metrics": {
        "store_count": 5,
        "monthly_revenue_per_store": 12,
        "gross_margin": "62.0%",
        "monthly_opex_per_store": 4.4,
        "monthly_net_profit_per_store": 3.0,
        "target_market_monthly_size": 600,
        "market_share": "20.0%",
        "core_product_price": "美式12元/杯",
        "amount_unit": "万元",
    },
    "nodes": NODES,
    "financial_results": [
        {"node_index": i, "option_key": k, "metrics": m} for i, k, m in RESULTS
    ],
    "standard_path": _standard_path(),
    "standard_review": STANDARD_REVIEW,
    # ── 标准化层（技术侧对齐，逐条标注来源） ──
    "simulation_results": SIMULATION_RESULTS,
    "standard_final_metrics": STANDARD_FINAL_METRICS,
    "standard_path_reasons": STANDARD_PATH_REASONS,
}

# ─────────────────────────────── 主流程 ───────────────────────────────


# ─────────────────── 商科待确认项（推导值清单，自动生成） ───────────────────

def _derivation_rows():
    """列出全部技术侧推导值：位置 / 字段 / 推导值 / 依据。"""
    rows = []
    for item in SIMULATION_RESULTS:
        ident = (item["node_index"], item["option_key"])
        if item["metric_source"]["cash_flow"] != "original":
            rows.append({
                "where": "标杆案例 · 节点%d · 选项%s（%s）" % (
                    item["node_index"], item["option_key"], item["risk_level"]),
                "field": "metrics.cash_flow",
                "value": item["metrics"]["cash_flow"],
                "basis": "原文表%d 没有现金流列；按 transmission_rules「%s」的现金流方向推导"
                         % (item["node_index"] + 5, CASH_FLOW_DERIVATION[ident]),
            })
    for ident, summary in SUMMARY_DERIVED.items():
        rows.append({
            "where": "标杆案例 · 节点%d · 选项%s" % ident,
            "field": "summary",
            "value": summary,
            "basis": "原文表6 没有「说明」列；技术侧按该行指标撰写",
        })
    rows.append({
        "where": "标杆案例 · 标准路径 B-B-B",
        "field": "standard_final_metrics.metrics.cash_flow",
        "value": STANDARD_FINAL_METRICS["metrics"]["cash_flow"],
        "basis": "原文表9 没有现金流列；%s" % STANDARD_FINAL_METRICS["cash_flow_derivation"],
    })
    rows.append({
        "where": "商科规则 · metric_formulas[*]",
        "field": "unit",
        "value": "百分比，保留一位小数 / 万元",
        "basis": "原文 §5.3 的公式表未标注单位；按 §3.4 的口径拼装",
    })
    rows.append({
        "where": "商科规则 · input_formats（纯文本）",
        "field": "rule",
        "value": "纯文本素材（系统实现对齐：T2 接受 .txt/.md 直读）",
        "basis": "原文 §4.1 只列了 Word/PDF/财报/新闻/关键词；纯文本属系统实现能力",
    })
    return rows


def build_pending_md():
    rows = _derivation_rows()
    lines = [
        '# 商科待确认项（技术侧推导值清单）',
        '',
        '> 本文件由 `docs/build_docs.py` 自动生成，请勿手工编辑；重新运行脚本即可刷新。',
        '>',
        '> 下列每一条都是**原文没有明确给出、由技术侧推导**的值。',
        '> 原文已写明的内容不在本清单内——它们全部逐字保留在 `docs/商科规则.md` '
        '与 `docs/标杆案例.json` 的 `source_*` 结构中。',
        '> 请商科组逐条确认：认可 / 给出正确值 / 驳回。',
        '',
        '## 一、原文缺失、技术侧推导的值（共 %d 条）' % len(rows),
        '',
        '| # | 位置 | 字段 | 推导值 | 推导依据 |',
        '|---|---|---|---|---|',
    ]
    for index, row in enumerate(rows, start=1):
        lines.append('| %d | %s | `%s` | %s | %s |' % (
            index, row["where"], row["field"], row["value"], row["basis"]))
    lines += [
        '',
        '## 二、来源判定口径',
        '',
        '- `source_status = "original"`：该选项四个指标全部有原文依据；',
        '- `source_status = "derived_from_transmission_rule"`：该选项至少一个指标是按 '
        '`transmission_rules` 推导的；',
        '- `metric_source` 逐指标给出出处，取值只有 `original` 与 '
        '`derived_from_transmission_rule` 两种；',
        '- `summary_source` 区分 `original`（原文「说明」列）与 `derived_by_technical_side`；',
        '- 技术侧**没有**把任何推导值标成 `original`，每次重新生成都会重新校验这一点。',
        '',
        '## 三、标准化口径（格式对齐，数值未变；不等于原文逐字一致）',
        '',
        '- 原文百分比写作 `63%`、`约20%`；标准化后写作 `"63.0%"`、`"20.0%"`'
        '（依据商科规范 §3.4「百分比保留一位小数」）；',
        '- 原文金额写作 `13万元`；标准化后写作 `13.0`（依据 §3.4「金额单位统一为万元」）；',
        '- 上述改写只动格式、不动数值；标准化值单独存放，原文写法完整保留在 `source_*` 中，不能把标准化值称为“逐字一致”。',
        '',
        '## 四、待商科组确认的复盘结构',
        '',
        '- SWOT / 4P / 盈亏平衡的维度数是否把“综合结论”计入？原文计数为 5/5/4；确认前 `dimensions` 按原文 5/5/4 传输，不拆分为“4+1 / 4+1 / 3+1”。',
        '',
    ]
    return '\n'.join(lines)


def main():
    for f in (DOC_RULES, DOC_CASE):
        if not f.is_file():
            print('缺少源文件：%s' % f, file=sys.stderr)
            return 1

    md_parts = [
        '<!-- 本文件由 docs/build_docs.py 从 docs/source/ 的两份 docx 机械转录生成，'
        '请勿手工编辑；需要改内容请改源文件后重新运行 python docs/build_docs.py -->',
        '',
        '# 商科规则（转录自 docs/source 两份商科文档）',
        '',
        '> 说明：正文与表格按原文档章节顺序逐字转录，未改写、未摘要、未省略任何数值。',
        '> 转录来源：',
        '> 1. `AI商科案例交互式推演系统-商科侧逻辑框架与指令规范.docx`',
        '> 2. `标杆案例-研咖咖啡·高校市场扩张与定价决策（技术调试标准版）.docx`',
        '>',
        '> 各文档的「目录」区未转录（属导航内容而非正文，且页码对 Markdown 无意义）；'
        '章节标题本身已完整保留。',
        '',
        '---',
        '',
        transcribe(DOC_RULES, '第一部分 · AI商科案例交互式推演系统——商科侧逻辑框架与指令规范'),
        '---',
        '',
        transcribe(DOC_CASE, '第二部分 · 标杆案例：研咖咖啡·高校市场扩张与定价决策（技术调试标准版）'),
    ]
    md_text = '\n'.join(md_parts)
    while '\n\n\n' in md_text:
        md_text = md_text.replace('\n\n\n', '\n\n')

    # 逐字校验：所有字符串值必须命中转录正文
    hay = norm(md_text)
    problems = []
    checked = 0

    def check(label, value):
        nonlocal checked
        checked += 1
        if norm(value) not in hay:
            problems.append('%s 未在转录正文中命中 -> %r' % (label, value))

    def check_source_strings(label, value):
        """source_results 全量逐字断言；标准化层不走这条断言。"""
        if isinstance(value, str):
            check(label, value)
        elif isinstance(value, dict):
            for key, item in value.items():
                check_source_strings('%s.%s' % (label, key), item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                check_source_strings('%s[%d]' % (label, index), item)

    # 回归断言：原有四组规则键仍逐字命中商科规则.md；新增推导字段不走这条断言。
    # 这样既保留历史基线，又不会把标准化/技术侧拼装值误当作原文。
    for legacy_key in ('case_types', 'industry_baseline',
                       'transmission_rules', 'review_templates'):
        check_source_strings('legacy.%s' % legacy_key, RULES[legacy_key])

    for rec in RULES["case_types"]:
        for k, v in rec.items():
            check('case_types.%s' % k, v)
    for rec in RULES["industry_baseline"]:
        for k, v in rec.items():
            check('industry_baseline.%s' % k, v)
    for rec in RULES["transmission_rules"]:
        for k, v in rec.items():
            check('transmission_rules.%s' % k, v)
    for rec in RULES["review_templates"]:
        check('review_templates.template', rec["template"])
        check('review_templates.conclusion_name', rec["conclusion_name"])
        for d in rec["dimensions"]:
            check('review_templates.dimension', d)

    # 2026-09-23 新增的四组规则
    for rec in RULES["metric_formulas"]:
        check('metric_formulas.formula', rec["formula"])
        check('metric_formulas.note', rec["note"])
    for rec in RULES["common_constraints"]:
        check('common_constraints.rule', rec["rule"])
    for rec in RULES["input_formats"]:
        if "系统实现" in rec["source"]:
            continue  # 「纯文本」是系统能力，不是商科原文，不做逐字校验
        check('input_formats.rule', rec["rule"])
    for rec in RULES["material_requirements"]:
        check('material_requirements.rule', rec["rule"])

    expected_review_counts = [5, 5, 4]
    actual_review_counts = [len(rec["dimensions"]) for rec in RULES["review_templates"]]
    if actual_review_counts != expected_review_counts:
        problems.append('review_templates 必须保持原文 5/5/4，实际为 %s' % actual_review_counts)

    # 标杆案例的原文层
    check_source_strings('source_results', CASE["source_results"])
    check('background', CASE["background"])
    check('dilemma', CASE["dilemma"])
    check('case_type', CASE["case_type"])
    check('base_metrics.core_product_price', CASE["base_metrics"]["core_product_price"])
    for n in NODES:
        for k in ("node_role", "title", "background"):
            check('node.%s' % k, n[k])
        for o in n["options"]:
            check('option.label', o["label"])
    for reason in STANDARD_PATH_REASONS:
        check('standard_path_reasons.reason', reason["reason"])
    for d in STANDARD_REVIEW["dimensions"]:
        check('standard_review.dimension.name', d["name"])
        check('standard_review.dimension.content', d["content"])
    check('standard_review.conclusion', STANDARD_REVIEW["conclusion"])

    # ── 标准化层：结构 + 数值必须能由原文单元格解析得到 ──
    if len(SIMULATION_RESULTS) != 9:
        problems.append('simulation_results 应为 9 条，实际 %d 条' % len(SIMULATION_RESULTS))
    for item in SIMULATION_RESULTS:
        required = {"option_key", "risk_level", "label", "summary", "metrics"}
        if not required.issubset(item):
            problems.append('simulation_results 缺少字段：%s' % sorted(required - set(item)))
        keys = set(item["metrics"].keys())
        if keys != {"revenue", "gross_margin", "market_share", "cash_flow"}:
            problems.append('simulation_results 的 metrics 键不对：%s' % sorted(keys))
        if item["metric_source"]["cash_flow"] == "derived_from_transmission_rule" \
                and item["source_status"] != "derived_from_transmission_rule":
            problems.append('推导现金流必须同步标记 source_status=derived_from_transmission_rule')
        if item["summary_source"] == "original" and norm(item["summary"]) not in hay:
            problems.append('summary 标为原文却未命中正文：%r' % item["summary"])

    def _first_number(text):
        found = re.findall(r"\d+(?:\.\d+)?", text or "")
        return float(found[0]) if found else None

    def _percent(text):
        value = _first_number(text)
        return None if value is None else "%.1f%%" % value

    for node_index, table in enumerate(SOURCE_NODE_RESULTS, start=1):
        header = table["header"]
        for raw_row in table["rows"]:
            cells = dict(zip(header, raw_row))
            letter = (cells.get("选项") or "")[:1]
            std = next((s for s in SIMULATION_RESULTS
                        if s["node_index"] == node_index and s["option_key"] == letter), None)
            if std is None:
                problems.append('节点%d 的原文行解析不出选项或缺少标准化结果：%r' % (node_index, raw_row))
                continue
            metric = std["metrics"]
            revenue = _first_number(cells.get("单店月营收"))
            if revenue is None or abs(metric["revenue"] - revenue) > 1e-9:
                problems.append('节点%d %s 营收对不上原文：%r vs %r'
                                % (node_index, letter, cells.get("单店月营收"), metric["revenue"]))
            if _percent(cells.get("毛利率")) != metric["gross_margin"]:
                problems.append('节点%d %s 毛利率对不上原文：%r vs %r'
                                % (node_index, letter, cells.get("毛利率"), metric["gross_margin"]))
            if _percent(cells.get("市场占有率")) != metric["market_share"]:
                problems.append('节点%d %s 市占率对不上原文：%r vs %r'
                                % (node_index, letter, cells.get("市场占有率"), metric["market_share"]))
            if "现金流表现" in cells:
                if cells["现金流表现"] != metric["cash_flow"]:
                    problems.append('节点%d %s 现金流与原文不一致：%r vs %r'
                                    % (node_index, letter, cells["现金流表现"], metric["cash_flow"]))
                if std["metric_source"]["cash_flow"] != "original":
                    problems.append('节点%d %s 的现金流来自原文，却被标为推导' % (node_index, letter))
            elif std["metric_source"]["cash_flow"] != "derived_from_transmission_rule":
                problems.append('节点%d %s 原文未给现金流，必须标为 derived_from_transmission_rule'
                                % (node_index, letter))

    if problems:
        print('逐字校验未通过，已中止落盘：')
        for p in problems:
            print('  [失败] ' + p)
        return 1

    (OUT / '商科规则.md').write_text(md_text, encoding='utf-8')
    (OUT / '商科规则.json').write_text(
        json.dumps(RULES, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / '标杆案例.json').write_text(
        json.dumps(CASE, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / '商科待确认项.md').write_text(build_pending_md(), encoding='utf-8')

    print('逐字校验通过：%d 个原文层字符串全部命中转录正文' % checked)
    print('商科规则.md      %d 字符' % len(md_text))
    print('商科规则.json    %s' % {k: len(v) for k, v in RULES.items()})
    print('标杆案例.json    nodes=%d options=%s simulation_results=%d' % (
        len(NODES), [len(n["options"]) for n in NODES], len(SIMULATION_RESULTS)))
    print('复盘维度数        %s（商科确认前按原文完整传输）'
          % [len(t["dimensions"]) for t in RULES["review_templates"]])
    print('推导值条数        %d 条（详见 docs/商科待确认项.md）' % len(_derivation_rows()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
