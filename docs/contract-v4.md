# case-sim 契约摘要

> 本页是 [SPEC.md](../SPEC.md) 的一页摘要，不替代完整契约。当前基线保持 8 张表；FastAPI 当前实现教师 T1–T9、学生 S1–S6 共 15 条业务接口，另有独立健康检查 `GET /api/health`；T10 为本步骤冻结的契约预留，后续步骤实现。

## S1：只下发题面

`GET /api/play/{student_token}` 只返回案例题面、节点和选项 `{key, label}`。不得提前下发任何未选择选项的 `risk_level`、`summary`、`metrics`。

自动化检查必须匹配带引号的完整键名（`"risk_level"`、`"summary"`、`"metrics"`），禁止裸子串匹配，否则 `base_metrics` 会被误报。

## S2：提交后返回本次已选结果

请求体固定为：

```json
{ "student_name": "…", "session_id": 1, "node_id": 2, "option_key": "B", "input_text": null, "duration_ms": 1200 }
```

客户端不得传入 `attempt_no`；一次 attempt 归属 `(case_id, student_token, student_name)`。同一 session 内刷新沿用当前 attempt，学生显式“再试一次”才由服务端分配新的 `attempt_no`。

响应中的 `result` 固定为：

```json
{
  "before_metrics": {},
  "after_metrics": {},
  "delta_metrics": {
    "revenue": 0,
    "gross_margin": 0,
    "market_share": 0,
    "cash_flow": {"from": "稳健", "to": "收紧→回正"}
  },
  "summary": "…",
  "source": "preset_option",
  "warning": null
}
```

预设选项的 `metrics` 是该节点选择后的目标状态快照，不是可直接相加的增量；下一回合 `before_metrics` 必须等于上一回合 `after_metrics`。`duration_ms` 必填且必须 `>= 0`。

## review：原文 5/5/4

`GET /api/play/{student_token}/review?session_id=` 返回 `{framework_type, dimensions, conclusion}`。商科组确认前，`dimensions` 和页面均按原文 SWOT 5、4P 5、盈亏平衡 4 传输与渲染，不得拆成 `4+1 / 4+1 / 3+1`。是否将“综合结论”从 `dimensions` 拆出，待商科组确认后再决定；`conclusion` 字段保留为兼容字段。

契约预留 `PATCH /api/reviews/{review_id}?token=` 供教师提交 `{dimensions?, conclusion?}`，响应为 `{ok:true}`；本步骤仅同步契约与类型，当前 FastAPI 尚未实现该路由。

## try-case：学生自建临时案例

S5 `POST /api/play/{student_token}/try-case` 与 S6 `GET /api/play/{student_token}/try/{try_id}` 以当前代码的 `TryCaseEcho` 为准：返回 `try_id`、案例类型、背景、基准数据和节点选项完整结果。内容只用于学生自己贴素材生成的试跑案例，暂存进程内，不写 8 张业务表；后续步骤再扩展成交互试跑。

## 案例版本冻结

`case.version` 创建时为 `1`；教师修改案例成功落库后递增。session 开始时记录 `case_version`、`case_snapshot_json` 与 `current_metrics_json`，已开始的 session 始终使用开局快照，不受教师后续修改影响。

## 数据源与推导标记

`rules.py` 消费 `docs/标杆案例.json` 的 `simulation_results`；`source_results` 仅作原文存档。现金流等技术侧推导值必须标记 `source_status="derived_from_transmission_rule"`，不得伪装为商科原文。
