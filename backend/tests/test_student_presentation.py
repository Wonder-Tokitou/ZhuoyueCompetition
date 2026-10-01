from types import SimpleNamespace

from backend.app.routers.student import _student_label
from backend.app.routers.student import _student_node


def test_student_labels_hide_risk_words_and_order_is_stable():
    assert _student_label("保守：控制投入，先验证市场", "A") == "控制投入，先验证市场"
    assert all(word not in _student_label("激进扩张方案", "B") for word in ("保守", "稳健", "激进"))

    node = SimpleNamespace(
        case_id=9,
        id=3,
        idx=1,
        scenario="场景",
        node_role="核心战略",
        title="节点",
        background="背景",
        option_results=[
            SimpleNamespace(option_key="A", label="保守：方案 A"),
            SimpleNamespace(option_key="B", label="稳健：方案 B"),
            SimpleNamespace(option_key="C", label="激进：方案 C"),
        ],
    )
    first = _student_node(node, "student-token")
    second = _student_node(node, "student-token")
    assert [item.key for item in first.options] == [item.key for item in second.options]
    assert {item.key for item in first.options} == {"A", "B", "C"}
    assert not any(word in item.label for item in first.options for word in ("保守", "稳健", "激进"))
