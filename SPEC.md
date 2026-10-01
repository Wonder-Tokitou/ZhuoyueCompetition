# case-sim · SPEC.md 接口契约

> 2026-09-25 架构修订：本文件的早期目录和账号范围是历史基线，不代表当前源码组织。当前分工与启动以 [架构分工与交接](docs/架构分工与交接.md) 为准；AI interface 见 [contracts](contracts/README.md)，当前 HTTP 模型以 backend/app/schemas.py 及路由回归测试为准。此次拆分保持既有 HTTP 路径和字段，不新增 AI 独立服务。

> 本文件是**步骤 1 产出**，是步骤 2–9 的唯一接口依据。
> 分工：本文件定义「表怎么建、接口长什么样」；代码不得偏离本文件的路径与字段名。
> 修改接口的顺序固定为：**先改本文件 → 再改代码**。
> 表结构字段与接口清单字段取自项目基线；基线未展开的嵌套结构在 §3 补齐。
> **本次修订（2026-09-22）**：按新基线 `docs/商科规则.json`（枚举与框架以它为准）、`docs/标杆案例.json` 增补表字段与接口。
> 已确认正确的既有约定一律**保持不变**：`case.status` 四态、`POST /api/cases` 走 multipart、教师端鉴权用 `?token=` 查询参数、`GET /api/cases` 返回裸数组、`GET /api/play/{student_token}` 一次性下发全部节点、`chat` 请求体 `{session_id, message}`、答案保密红线、前端产物路径 `frontend/dist`。

---

## 0. 项目基线（所有步骤共用）

| 项 | 约定 |
|---|---|
| 仓库根目录 | `.`（当前项目根目录） |
| 后端 | FastAPI + SQLAlchemy 2 + SQLite + Pydantic v2 + httpx |
| 前端 | React 18 + Vite + TypeScript + Ant Design 5 + axios |
| 模型接口 | OpenAI 兼容：`POST {LLM_BASE_URL}/chat/completions` |
| 环境变量 | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` |
| 端口 | 只暴露一个：前端 `npm run build` 产物由 FastAPI 静态托管在 `/`，接口挂在 `/api` |
| 双端区分 | 链接区分，不做账号系统：`/teacher/{teacher_token}`、`/student/{student_token}` |
| 语言 | 代码注释、变量命名外的 UI 文案一律中文 |
| 初赛明确不做 | 账号注册登录、统计看板、模型本地部署或微调、校友案例库 |

### 0.1 目录结构（目标态，随步骤逐步落地）

```
case-sim/
├─ SPEC.md                     # 接口契约（步骤 1 产出，后续所有步骤的输入）
├─ backend/
│  ├─ requirements.txt
│  ├─ .env.example
│  └─ app/
│     ├─ main.py               # FastAPI 实例 + 静态托管
│     ├─ db.py                 # engine / SessionLocal / get_db
│     ├─ models.py             # 8 张表
│     ├─ schemas.py            # Pydantic 模型
│     ├─ routers/teacher.py
│     ├─ routers/student.py
│     └─ services/llm.py       # LLM 调用 + 结构化输出校验重试降级
├─ frontend/
│  └─ src/
│     ├─ api/client.ts
│     ├─ pages/teacher/        # 工作台、记录页
│     └─ pages/student/        # 推演页、复盘页
└─ start.bat                   # 一键启动
```

> 步骤 1 只落地 `backend/app/{routers,services}` 与 `frontend/` 目录；其余文件由步骤 2、3、4、5、8 逐步补齐。

### 0.2 全局约定

- **接口计数口径固定为：当前实现 15 条业务接口（教师端 T1–T9、学生端 S1–S6）+ 1 条独立健康检查 `GET /api/health`；T10 为本步骤冻结的契约预留，尚未加入 FastAPI 路由。** 前端路由 = `/teacher/{token}`、`/student/{token}`（SPA 页面，不是接口）；前端路由由 `index.html` 兜底，不占后端接口位。健康检查不列入下面的业务接口序号。
- **鉴权一律走查询参数 `?token=`**，不用请求头。当前已实现且带 `?token=` 的教师端接口为 **T3–T9**；契约预留的 T10 也沿用该鉴权方式（见 §2）。T1（登录本身）与 T2（创建案例）不带。学生端靠路径中的 `student_token` 识别，不再要 token 参数。
- **命名映射（全文最关键的一条）**：数据库列名与 API 字段名不完全同名，映射固定如下，不得混用。

  | 数据库列 | API 字段 | 说明 |
  |---|---|---|
  | `node.options_json` | `options` | 出参统一叫 `options` |
  | `option_result.option_key` | `key` | 选项标识 `A`/`B`/`C` |
  | `option_result.metrics_json` | `metrics` | 指标对象 |
  | `option_result.summary` | `summary` | 同名，直接透传 |
  | `turn.result_json` | `result_json` | 同名，直接透传 |

- **指标固定四个键**（口径与方向枚举以 `docs/商科规则.json` 的 `transmission_rules` 为准）：

  | 键 | 含义 | 类型 | 单位 / 格式 |
  |---|---|---|---|
  | `revenue` | 营收 | number | 万元 |
  | `gross_margin` | 毛利率 | string | 百分比，保留一位小数，如 `"62.0%"` |
  | `market_share` | 市场份额 | string | 百分比，保留一位小数，如 `"20.0%"` |
  | `cash_flow` | 现金流 | string | 方向描述文案，如 `"短期收紧"` |

- **`cash_flow` 跨层类型固定为字符串**：若作为数据库独立文案列，列类型为 `TEXT`；在 `metrics_json` / `before_metrics_json` / `after_metrics_json` / `delta_metrics_json` 等 JSON 对象中为 JSON string；API 中始终为 string。它表达文字方向（例如 `"稳健"` 或 `"收紧→回正"`），不是数值。
- **金额单位统一为万元；百分比一律用字符串并保留一位小数。** 前端数据看板展示 `revenue` / `gross_margin` / `market_share` 三项，`cash_flow` 以文案展示。
- **`metrics` 的语义是「该节点选择后的目标状态快照」，不是可直接相加的增量。**
  选项上的 `metrics` 表示「选了它之后，企业处于什么状态」；要做增减必须由服务端算
  `after_metrics − before_metrics`（见 §1.5「指标状态机」与 T6 之后各节的 `delta_metrics`）。
  **不得把选项的 `metrics` 直接累加到当前状态上。**
- **选项后果对象**（出现于教师端与 S2 的 `after_metrics`）：`{"metrics": {...}, "summary": string}`。
- **时间**：数据库 `DATETIME`，出参统一 ISO 8601 字符串（如 `2026-09-22T21:14:47`）；未发生为 `null`。
- **错误响应**：HTTP 4xx/5xx，响应体固定 `{"detail": "<中文说明>"}`（`HTTPException` 默认形状）。前端直接用 `message.error(detail)` 展示。
- **学生端保密红线（精确口径）**：**只有 S1 不得提前下发未选择选项的 `risk_level` / `summary` / `metrics`；S2 可返回本次已选结果；学生自建案例接口可返回自己生成的结果。** `base_metrics` 属题面基准数据，任何学生端接口都可下发。
  自动化检查必须匹配带引号的完整键名（`"risk_level"`、`"summary"`、`"metrics"`），禁止裸子串匹配，否则 `base_metrics` 会被误报。
  检查键名必须使用完整带引号形式（"risk_level"、"summary"、"metrics"），禁止裸子串匹配，否则 `base_metrics` 会被误报。
  精确红线原文：“只有 S1 不得提前下发未选择选项的 risk_level / summary / metrics；S2 可返回本次已选结果；学生自建案例接口可返回自己生成的结果。”
- **建表**：SQLAlchemy `create_all()`，**不引入 alembic**。
- **保留字**：表名 `case`、列名 `idx` 在 SQLite 中是保留字，写原生 SQL 必须加引号（ORM 自动处理）。
- **V3 财务区间校验口径**：判据是「毛利率与营收落在 `industry_baseline` 中**任意一个**行业的区间内」即为通过。`case_type`（分析框架分类）与行业分类是**正交维度**，不存在 `case_type → 行业` 的映射，**不要新增该映射表**。
- **学生端首屏所需信息全部来自 `case` 表**：`case_title` / `case_type` / `background` / `dilemma` / `base_metrics` 属题面信息，允许下发；**唯一禁止提前下发的是 S1 的未选选项结果**（见上方「答案保密口径」）。
- **案例版本冻结**：`case.version` 是题面版本号；教师每次改动（T5 / T6 成功落库）**+1**。session 开局时把 `case.version` 记进 `session.case_version`、把题面快照记进 `session.case_snapshot_json`；**教师之后改案例不影响已开始的 session**。

### 0.3 标杆数据消费与文档断言策略

- **业务数据源**：`rules.py` 读取 `docs/标杆案例.json` 的 `simulation_results`；`source_results` 仅作原文存档，**不被业务逻辑消费**。
- **`docs/build_docs.py` 断言**：`source_*` 字段继续保留“逐字命中 md 正文”的断言；`simulation_results` 等推导字段改用“结构断言 + 校验 `source_status` 标记”。同时保留回归断言：原有 4 个键仍须逐字命中 md。

---

## 1. 表结构（8 张）

> 各表新增字段一律**追加在表格末尾**，既有字段顺序不变；每张改动过的表下方标注本次新增了哪些字段。

### 1.1 `framework` — 商科分析框架

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 框架 ID |
| `name` | VARCHAR(128) | 非空 | 框架名称，取值同 `商科规则.json` 的 `review_templates.template`：`SWOT分析模型` / `4P营销理论` / `盈亏平衡分析` |
| `prompt_template` | TEXT | 非空 | 该框架的提示词模板 |
| `output_schema` | JSON | 非空 | 该框架要求的输出结构约束 |
| `version` | VARCHAR(32) | 非空，默认 `v1` | 框架版本 |
| `created_at` | DATETIME | 非空 | 创建时间 |

### 1.2 `case` — 教学案例

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 案例 ID |
| `title` | VARCHAR(200) | 非空 | 案例标题 |
| `source_text` | TEXT | 非空 | 原始案例素材全文（PDF 抽取后也存这里） |
| `status` | VARCHAR(16) | 非空，默认 `draft` | **枚举四态：`draft` / `generating` / `ready` / `published`** |
| `framework_id` | INTEGER | 外键 → `framework.id`，可空 | 所用框架 |
| `teacher_token` | VARCHAR(64) | 非空，唯一 | 教师端令牌（`login` 返回，前端放 URL） |
| `student_token` | VARCHAR(64) | 非空，唯一 | 学生端推演令牌（`publish` 时生成） |
| `created_at` | DATETIME | 非空 | 创建时间 |
| `published_at` | DATETIME | 可空 | 发布时间；未发布为 `null` |
| `case_type` | VARCHAR(32) | 非空 | **案例类型**，枚举：`战略决策类` / `市场营销类` / `财务管理类`（取自 `商科规则.json` 的 `case_types.case_type`） |
| `source_kind` | VARCHAR(16) | 非空 | **素材来源**，枚举：`material`（上传文档/粘贴文字）/ `topic`（仅给主题关键词） |
| `base_metrics_json` | JSON | 可空 | **基准数据**，生成完成后必有值；键同 §0.2 的四指标（**一个都不能少**，缺项触发重新生成） |
| `owner_type` | VARCHAR(24) | 非空，默认 `teacher` | **案例归属**，枚举：`teacher`（教师正式案例，可发布入库）/ `student_ephemeral`（学生自助试算，不落案例库） |
| `background` | TEXT | 非空 | **企业背景**；模型输出的一部分，学生端首屏背景卡片直接用这一列 |
| `dilemma` | TEXT | 非空 | **核心经营困境**；学生端首屏困境卡片直接用这一列 |
| `version` | INTEGER | 非空，默认 `1` | **案例版本号**。创建时为 `1`；每一次教师改动（T5 或 T6 成功落库）**+1**。已开始的 session 用 `session.case_version` 记住自己起步时的版本，不会被后续改动影响 |

> 本次修订（2026-09-23）：新增 `version`（追加在表末）。
> `version` 是「案例版本冻结」的依据：教师改了案例内容，`version` 递增；已开局的 session 不跟着变。

> 本次新增：`case_type`、`source_kind`、`base_metrics_json`、`owner_type`（追加在原 9 列之后，原字段顺序未变）。
> 本次再新增：`background`、`dilemma`（追加在表末，原字段顺序一字未动）。
> 注意：`background` / `dilemma` 在「建行」时先写入空串（表结构与 `status=generating` 需要先有行），生成成功后用模型输出覆盖；生成失败的案例这两列保持空串。

**`status` 四态流转**：`draft`（刚创建/生成失败）→ `generating`（正在调模型）→ `ready`（节点已就绪，可校准）→ `published`（已发布，节点冻结）。

### 1.3 `node` — 推演节点（决策点）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 节点 ID |
| `case_id` | INTEGER | 外键 → `case.id`，非空 | 所属案例 |
| `idx` | INTEGER | 非空 | 顺序号，从 1 开始；唯一键 `(case_id, idx)` |
| `scenario` | TEXT | 非空 | 呈现给学生的情境描述 |
| `options_json` | JSON | 非空 | 选项数组，元素 `{key, label}`；**不含任何后果信息** |
| `created_at` | DATETIME | 非空 | 创建时间 |
| `node_role` | VARCHAR(16) | 非空 | **节点角色**，枚举：`核心战略` / `核心策略` / `落地执行`（按 `idx` = 1/2/3 固定对应） |
| `title` | VARCHAR(200) | 非空 | 节点标题，如「市场扩张策略」 |
| `background` | TEXT | 非空 | 节点背景说明，供前端在节点内展示 |

> 本次新增：`node_role`、`title`、`background`（追加在原 6 列之后，原字段顺序未变）。

> **注**：`scenario` 与 `background` **同源**——由同一段模型输出（节点背景说明）同时写入两列；前端统一使用 `background`。生成链路不再单独产出「情境描述」。

> 生成约束（步骤 4）：每个案例固定 **3 个节点**，每个节点固定 **3 个选项**（`key` 为 `A`/`B`/`C`，依次对应 `保守` / `稳健` / `激进`）。

### 1.4 `option_result` — 选项后果（答案，学生端严格保密）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 记录 ID |
| `node_id` | INTEGER | 外键 → `node.id`，非空 | 所属节点 |
| `option_key` | VARCHAR(8) | 非空 | 选项标识，与 `node.options_json` 中的 `key` 一一对应；唯一键 `(node_id, option_key)` |
| `label` | VARCHAR(200) | 非空 | 选项文案（冗余存储，便于直接查表核对） |
| `metrics_json` | JSON | 非空 | 指标变化，固定四键 `revenue` / `gross_margin` / `market_share` / `cash_flow` |
| `risk_level` | VARCHAR(8) | 非空 | **风险等级**，枚举：`保守` / `稳健` / `激进`（同一节点下三档各恰 1 行，唯一键 `(node_id, risk_level)`） |
| `summary` | TEXT | 非空 | 该选项的结果摘要，必填；供教师端校准与前端结果提示展示 |

> 本次二次修订：结果文案只保留 `summary` 这一个字段；`risk_level` 与 `summary` 追加在表末，其余字段顺序未变。

> 本表**无 `created_at`**（基线如此定义）。生成约束：每案例 3 节点 × 3 选项 = **9 行**。

### 1.5 `session` — 一次学生推演

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 推演 ID（即接口里的 `session_id`） |
| `case_id` | INTEGER | 外键 → `case.id`，非空 | 推演所属案例 |
| `student_name` | VARCHAR(64) | 非空 | 学生姓名（姓名 Modal 采集，不做账号） |
| `created_at` | DATETIME | 非空 | 开始时间 |
| `finished_at` | DATETIME | 可空 | 写入时机见下方说明；未走完为 `null`（`review` 据此判断） |
| `attempt_no` | INTEGER | 非空，默认 `1` | **第几次尝试**。一次 attempt 归属 `(case_id, student_token, student_name)`；由服务端计算，客户端不得指定（见 S2） |
| `case_version` | INTEGER | 非空 | 该次推演**起步时**的 `case.version`，用于版本冻结 |
| `case_snapshot_json` | JSON | 非空 | 开局时的题面快照：`{title, case_type, background, dilemma, base_metrics, nodes:[{id, idx, node_role, title, background, options:[{key, label}]}]}`。教师之后改案例不影响本 session 的题面 |
| `current_metrics_json` | JSON | 非空 | 该 session 当前的**指标状态**（四键）。取值规则见下方「指标状态机」 |

> 本次修订（2026-09-23）：新增 `case_version`、`case_snapshot_json`、`current_metrics_json`（追加在表末）。
> **attempt 语义**：同一 session 内刷新沿用当前 attempt；学生显式“再试一次”时由服务端分配新的 `attempt_no`（并建立新的尝试记录）。一次 session 代表一次完整尝试（三个节点走完一次）。

**`finished_at` 写入时机（本次修订明确）**：只在该次推演的**最后一个节点**提交成功时写入——即 `decide` 的响应中 `next_node_id` 为 `null`（三个节点已走完）的那一刻。中途退出、或只走到第 1、2 个节点时保持 `null`。

**指标状态机（`current_metrics_json` 的维护规则）**：

- 新建 session 时，`current_metrics_json` = `case.base_metrics` 的快照；
- 每提交一个节点：`before_metrics` = 当前的 `current_metrics_json`，`after_metrics` = 该选项的**目标状态快照**，`delta_metrics` = `after − before`；
- 提交后把 `current_metrics_json` 更新为本次的 `after_metrics`；
- 因此**下一回合的 `before_metrics` 恒等于上一回合的 `after_metrics`**。

### 1.6 `turn` — 一次决策回合

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 回合 ID |
| `session_id` | INTEGER | 外键 → `session.id`，非空 | 所属推演 |
| `node_id` | INTEGER | 外键 → `node.id`，非空 | 所在节点 |
| `input_text` | TEXT | 可空 | 学生自定义决策文本；未填为 `null` |
| `chosen_option` | VARCHAR(8) | 非空 | 所选 `key`；**走「自定义决策」时记空字符串 `""`** |
| `result_json` | JSON | 非空 | 该回合结果快照：`{metrics, summary}` |
| `duration_ms` | INTEGER | 非空 | 决策耗时（毫秒）；**必填且 `>= 0`**（见 S2） |
| `created_at` | DATETIME | 非空 | 提交时间 |
| `attempt_no` | INTEGER | 非空，默认 `1` | **第几次尝试**，与所属 `session.attempt_no` 一致；归属键为 `(case_id, student_token, student_name)` |
| `before_metrics_json` | JSON | 非空 | 本回合**选择前**的指标状态（四键），即上一回合的 `after_metrics`；首回合取 `case.base_metrics` |
| `after_metrics_json` | JSON | 非空 | 本回合**选择后**的指标状态（四键），等于所选选项的 `metrics`（**目标状态快照**） |
| `delta_metrics_json` | JSON | 非空 | `after_metrics` 相对 `before_metrics` 的变化：`revenue` 数值差、`gross_margin` / `market_share` 为解析百分比后的百分点差数值、`cash_flow` 为 `{from, to}` 两个字符串 |
| `result_source` | VARCHAR(24) | 非空 | 结果来源，枚举：`preset_option`（选了预设选项）/ `custom_input`（自由输入，无预设测算） |

> 本次修订（2026-09-23）：新增 `before_metrics_json`、`after_metrics_json`、`delta_metrics_json`、`result_source`（追加在表末）。
> `result_json` 作为**兼容字段保留不删**，内容为 `{metrics, summary}`；新代码一律用上面三个 `*_metrics_json` 与 `result_source`。
> 前端数据看板读 `delta_metrics` 做增减动画（**涨红跌绿**）。

### 1.7 `message` — 对话消息（AI 助教答疑）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 消息 ID |
| `session_id` | INTEGER | 外键 → `session.id`，非空 | 所属推演 |
| `role` | VARCHAR(16) | 非空 | 枚举：`user`（学生）/ `assistant`（AI 助教）/ `system`（系统提示） |
| `content` | TEXT | 非空 | 消息正文 |
| `created_at` | DATETIME | 非空 | 消息时间；SSE 流式输出完毕后落库 |

### 1.8 `review` — 复盘报告（本次新增表）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键，自增 | 复盘 ID |
| `session_id` | INTEGER | 外键 → `session.id`，非空 | 所属推演 |
| `attempt_no` | INTEGER | 非空，默认 `1` | **第几次尝试**，与 `session.attempt_no` 对应；唯一键 `(session_id, attempt_no)` |
| `framework_type` | VARCHAR(32) | 非空 | **复盘所用框架**，枚举：`SWOT分析模型` / `4P营销理论` / `盈亏平衡分析`（取自 `商科规则.json` 的 `review_templates.template`，与案例 `case_type` 一一对应） |
| `dimensions_json` | JSON | 非空 | **分析维度数组**，元素 `{"name": string, "content": string}`；商科组确认前按源文档完整传输 SWOT 5 / 4P 5 / 盈亏平衡 4，包含原文「综合结论」项；是否拆出待确认，**不得自行拆分为“4+1 / 4+1 / 3+1”** |
| `conclusion` | TEXT | 非空 | 兼容字段，保存原文综合结论文本；确认前不作为额外页面块，避免与 `dimensions_json` 重复渲染 |
| `created_at` | DATETIME | 非空 | 生成时间 |

> 本表整体为本次修订新增。生成时机：该次尝试走完三个节点（即 `session.finished_at` 已写入）后，由 `GET /api/play/{student_token}/review` 触发生成并落库；同一 `(session_id, attempt_no)` 已存在时直接返回历史结果，不重复调用模型。

**复盘模板维度清单**（取自 `docs/商科规则.json` 的 `review_templates`，**不是第 9 张表**，只是常量清单）：

| `template`（`framework_type`） | `dimensions`（原文顺序，含综合结论） | `conclusion_name` | 原文计数（待确认） |
|---|---|---|---|
| `SWOT分析模型` | `优势（S）`、`劣势（W）`、`机会（O）`、`威胁（T）`、`综合结论` | `综合结论` | 5 |
| `4P营销理论` | `产品（Product）`、`价格（Price）`、`渠道（Place）`、`促销（Promotion）`、`综合结论` | `综合结论` | 5 |
| `盈亏平衡分析` | `成本端`、`销量端`、`盈利端`、`综合结论` | `综合结论` | 4 |

> 商科组确认前只采用上表原文计数（SWOT 5 / 4P 5 / 盈亏平衡 4），`dimensions_json` 按原文 5/5/4 传输；是否将综合结论从 `dimensions` 拆出，待商科组确认后再决定。不得自行拆分为“4+1 / 4+1 / 3+1”。

---

## 2. 接口清单（15 条已实现业务接口 + 1 条 T10 契约预留）

### 2.1 教师端（10 条）

| 序号 | 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|---|
| T1 | POST | `/api/teacher/login` | `{password}` | `{teacher_token}` |
| T2 | POST | `/api/cases` | multipart：`title` + `case_type` + `file` 或 `text`（+ `framework_id`） | `{case_id, status}` |
| T3 | GET | `/api/cases?token=` | — | `[{id,title,status,created_at}]` |
| T4 | GET | `/api/cases/{case_id}?token=` | — | `{case, nodes:[{id,idx,scenario,options}]}` |
| T5 | PATCH | `/api/cases/{case_id}?token=` | `{title?, base_metrics?}` | `{ok:true}` |
| T6 | PATCH | `/api/cases/{case_id}/nodes/{node_id}?token=` | `{scenario?, options?}` | `{ok:true}` |
| T7 | POST | `/api/cases/{case_id}/ai-fix?token=` | `{node_id?, message}` | `{reply}` |
| T8 | POST | `/api/cases/{case_id}/publish?token=` | — | `{student_url, qr_code_url}` |
| T9 | GET | `/api/cases/{case_id}/records?token=` | — | `[{student_name, attempt_no, turns:[...]}]` |
| T10 | PATCH | `/api/reviews/{review_id}?token=` | `{dimensions?, conclusion?}` | `{ok:true}` |

### 2.2 学生端（6 条）

| 序号 | 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|---|
| S1 | GET | `/api/play/{student_token}` | — | `{case_title, nodes:[{id,idx,scenario,options}]}` |
| S2 | POST | `/api/play/{student_token}/decide` | `{student_name, session_id?, node_id, option_key?, input_text?, duration_ms}` | `{session_id, result:{before_metrics, after_metrics, delta_metrics, summary, source, warning?}, next_node_id}` |
| S3 | POST | `/api/play/{student_token}/chat` | `{session_id, message}` | SSE 流 |
| S4 | GET | `/api/play/{student_token}/review?session_id=` | — | `{framework_type, dimensions:[{name,content}], conclusion}` |
| S5 | POST | `/api/play/{student_token}/try-case` | `{text, title?, case_type}` | `{try_id, nodes:[…]}` |
| S6 | GET | `/api/play/{student_token}/try/{try_id}` | — | `{try_id, nodes:[…]}` |

> 本次修订：教师端新增 T5，学生端新增 S5、S6，原 S4 由 `summary` 改名为 `review`（T 序号因插入 T5 而顺延，详情小节同步重排）。
> 本次契约补充 T10：教师可修改学生复盘；T10 在本步骤只冻结契约，尚未实现 FastAPI 路由，后续步骤再落地。健康检查 `GET /api/health` 仍是独立运维路由，不计入业务接口序号。当前实现业务接口为 15 条，契约编号总数为 16 条，8 张表不变。

---

## 3. 接口详情（请求体 / 响应体字段）

### T1 `POST /api/teacher/login`

- 请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `password` | string | 是 | 教师口令，与后端环境变量 `TEACHER_PASSWORD` 比对 |

- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `teacher_token` | string | 教师端令牌；步骤 2 阶段固定返回 `"dev-teacher-token"` |

- 失败：`401 {"detail": "口令错误"}`

---

### T2 `POST /api/cases`

- `Content-Type: multipart/form-data`
- 请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 是 | 案例标题 |
| `case_type` | string | 是 | **案例类型**，取值：`战略决策类` / `市场营销类` / `财务管理类`；缺失返回 `400 {"detail": "请指定案例类型"}` |
| `text` | string | 二选一 | 纯文本素材；与 `file` **必须提供其一** |
| `file` | UploadFile | 二选一 | 素材文件，接受 `.txt` / `.md` / `.pdf`；PDF 用 pypdf 抽文字 |
| `framework_id` | integer | 否 | 指定框架；不传则用内置兜底模板 |

- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | integer | 新建案例 ID |
| `status` | string | 四态之一：`draft` / `generating` / `ready` / `published` |

- 失败与降级：
  - `400 {"detail": "请提供可复制的文字版素材"}` —— 抽取到的文字少于 **200 字**（PDF 无文字层走这条）。
  - `200 {"case_id": n, "status": "draft", "warning": "AI 生成失败，可重试"}` —— 模型调用失败时**不得返回 500**，`status` 回到 `draft`，附加 `warning` 字段（`warning` 仅在失败时出现）。
- 语义：成功时应为 `generating` → `ready`（异步或同步皆可），落库 3 个 `node` + 9 个 `option_result`。

---

### T3 `GET /api/cases?token=`

- 请求体：无；查询参数：`token`，string（必填）
- 响应体：**裸数组**（不是 `{items: [...]}`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 案例 ID |
| `title` | string | 案例标题 |
| `status` | string | 四态之一 |
| `created_at` | string | 创建时间（ISO 8601） |

---

### T4 `GET /api/cases/{case_id}?token=`

- 路径参数：`case_id`，integer；查询参数：`token`，string（必填）
- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case` | object | 见下方 `case` 字段表 |
| `nodes` | array | 该案例全部节点，按 `idx` 升序；元素见下方 `nodes` 字段表 |

`case` 字段（对应 `case` 表全部 15 列）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 案例 ID |
| `title` | string | 标题 |
| `source_text` | string | 原文素材（供老师校准对照） |
| `status` | string | 四态之一 |
| `framework_id` | integer \| null | 所用框架 ID |
| `teacher_token` | string | 教师端令牌 |
| `student_token` | string | 学生端令牌 |
| `created_at` | string | 创建时间 |
| `published_at` | string \| null | 发布时间 |
| `case_type` | string | 案例类型（三选一） |
| `source_kind` | string | `material` / `topic` |
| `base_metrics` | object \| null | 基准数据（`case.base_metrics_json`）；键同 §0.2 四指标 |
| `owner_type` | string | `teacher` / `student_ephemeral` |
| `background` | string | 企业背景 |
| `dilemma` | string | 核心经营困境 |

`nodes` 元素字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 节点 ID |
| `idx` | integer | 顺序号，从 1 开始 |
| `scenario` | string | 情境描述 |
| `node_role` | string | 节点角色，取值同 §1.3：`核心战略` / `核心策略` / `落地执行` |
| `title` | string | 节点标题，如「市场扩张策略」 |
| `background` | string | 节点背景说明 |
| `options` | array | **教师端**选项数组，元素 `{key, label, risk_level, metrics, summary}` —— 老师必须看到风险等级与后果才能校准 |

- 失败：`404 {"detail": "案例不存在"}` / `403 {"detail": "令牌无效"}`

---

### T5 `PATCH /api/cases/{case_id}?token=`（本次新增）

- 路径参数：`case_id`，integer；查询参数：`token`，string（必填）
- 请求体（两字段均可选，只传要改的）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 否 | 覆盖 `case.title` |
| `base_metrics` | object | 否 | 整组覆盖 `case.base_metrics_json`；键为 §0.2 的四指标，**四项一个都不能少** |

- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 固定 `true` |

- 与 T6 的分工：**T5 改案例级信息（标题、基准数据），T6 改节点级信息（情境、选项）**。
- 已发布（published）的案例同样允许编辑；编辑后学生端下次进入即看到最新版本。
- **版本冻结**：本接口成功落库后 `case.version` **+1**；已经开始的 session 不受影响（见 §0.2「案例版本冻结」与 §1.5）。

---

### T6 `PATCH /api/cases/{case_id}/nodes/{node_id}?token=`

- 路径参数：`case_id`、`node_id`，均 integer；查询参数：`token`，string（必填）
- 请求体（四个字段均可选，只传要改的）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 否 | 覆盖 `node.title` |
| `background` | string | 否 | 覆盖 `node.background` 与 `node.scenario`（两者同源，一起写） |
| `scenario` | string | 否 | 覆盖 `node.scenario`（兼容保留，与传 `background` 等价） |
| `options` | array | 否 | 整组替换该节点的 `option_result`（**先删后插**）；元素 `{key, label, risk_level, metrics, summary}`，`risk_level` 三档 `保守`/`稳健`/`激进` 必须各给一个 |

> 前端只使用 `title` 与 `background`；`scenario` 为兼容保留，传它与传 `background` 等价，都写两列。

- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 固定 `true` |

- 已发布（published）的案例同样允许编辑；编辑后学生端下次进入即看到最新版本。
- **版本冻结**：本接口成功落库后 `case.version` **+1**；已经开始的 session 不受影响（见 §0.2「案例版本冻结」与 §1.5）。

---

### T7 `POST /api/cases/{case_id}/ai-fix?token=`

- 路径参数：`case_id`，integer；查询参数：`token`，string（必填）
- 请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `node_id` | integer | 否 | 要讨论的节点；不传则只按 `message` 泛问 |
| `message` | string | 是 | 老师的问题，如「这个节点的选项区分度太低」 |

- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `reply` | string | 模型返回的修正建议文本 |

- 语义：**只出建议，不自动改库**。老师确认后由 T5（案例级）或 T6（节点级）落库。

---

### T8 `POST /api/cases/{case_id}/publish?token=`

- 路径参数：`case_id`，integer；查询参数：`token`，string（必填）
- 请求体：无
- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `student_url` | string | 学生端链接路径，格式固定 `"/student/{student_token}"` |
| `qr_code_url` | string | 该学生端链接对应的二维码图片地址，供课堂投屏与分享；教师端直接以 `<img src>` 引用（**本次修订新增**） |

- 语义：生成 **32 位随机** `student_token`，`status` 置 `published`，写 `published_at`；随后按 `student_url` 生成二维码并返回可访问的 `qr_code_url`。
- 重复发布：对已 `published` 的案例再次调用时**复用原 `student_token`**，不重新生成，避免已分发的链接与二维码失效。

---

### T9 `GET /api/cases/{case_id}/records?token=`

- 路径参数：`case_id`，integer；查询参数：`token`，string（必填）
- 请求体：无
- 响应体：**裸数组，按 `session` 分组**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `student_name` | string | 学生姓名（同案例多个学生即多个元素） |
| `attempt_no` | integer | 第几次尝试；按 `(case_id, student_token, student_name)` 归属并由服务端计算，同一 session 刷新沿用，显式“再试一次”才分配新值 |
| `turns` | array | 该组的回合列表，按 `created_at` 升序 |

`turns` 元素字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | `turn.id` |
| `node_id` | integer | 节点 ID |
| `idx` | integer | 节点顺序号（记录页「节点序号」列） |
| `chosen_option` | string | 所选 `key`；自定义决策为空字符串 |
| `input_text` | string \| null | 自定义决策文本 |
| `before_metrics` | object | 选择前指标状态（四键） |
| `after_metrics` | object | 选择后指标状态（四键） |
| `delta_metrics` | object | 相对变化（四键） |
| `result_source` | string | `preset_option` / `custom_input` |
| `result_json` | object | `{metrics, summary}` 快照（兼容字段） |
| `duration_ms` | integer | 用时（记录页「用时」列） |
| `created_at` | string | 提交时间 |

---

### T10 `PATCH /api/reviews/{review_id}?token=`

教师对已生成的学生复盘做人工修正；本步骤只冻结契约，不实现业务逻辑。

- 路径参数：`review_id`，integer；查询参数：`token`，string（必填）
- 请求体：`{dimensions?, conclusion?}`，两字段均可选，只传要改的字段；`dimensions` 在商科确认前按原文 5/5/4 结构传输。
- 响应体：`{ok: true}`
- 失败：`404`（复盘不存在）/ `403`（令牌无效）/ `400`（复盘结构不符合当前模板）

---

### S1 `GET /api/play/{student_token}`

- 路径参数：`student_token`，string
- 请求体：无
- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_title` | string | 案例标题 |
| `case_type` | string | 案例类型（三选一），学生端用于显示框架标签 |
| `background` | string | 企业背景，供学生首屏背景卡片 |
| `dilemma` | string | 核心经营困境，供学生首屏困境卡片 |
| `base_metrics` | object \| null | 四项基准数据（键同 §0.2），供学生端指标条初值 |
| `nodes` | array | 该案例**全部**节点，按 `idx` 升序（一次下发，前端本地推进） |

`nodes` 元素字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 节点 ID |
| `idx` | integer | 顺序号，从 1 开始 |
| `scenario` | string | 情境描述 |
| `node_role` | string | 节点角色，取值同 §1.3：`核心战略` / `核心策略` / `落地执行` |
| `title` | string | 节点标题，如「市场扩张策略」 |
| `background` | string | 节点背景说明 |
| `options` | array | **学生端**选项数组，元素**只有** `{key, label}` |

- 失败：`404 {"detail": "推演链接无效"}` —— 令牌不存在或案例非 `published` 状态
- **红线（唯一一条提前保密约束）**：S1 只下发**题面 + 节点 + 选项文案**，
  响应文本中不得出现带引号的键名 `"risk_level"`、`"metrics"`、`"summary"`。
  **不得提前下发任何未选择选项的结果**。
- **例外说明**：`base_metrics` 是题面基准数据（不是答案），字段名虽含 `metrics` 字样，属允许下发的字段；
  做自动化检查时必须用**带引号的完整键名**匹配，不要用裸串 `metrics` 直接判违规（必然误报）。
- **注意**：这只是 S1 一条的约束。S2 提交后返回本次已选结果、S5/S6 返回学生自建案例的结果，都是允许的（见 §0.2「答案保密口径」）。

---

### S2 `POST /api/play/{student_token}/decide`

- 路径参数：`student_token`，string
- 请求体（字段顺序即契约顺序）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `student_name` | string | 是 | 姓名；`session_id` 不传时用于新建 `session` |
| `session_id` | integer | 否 | 已有推演时必传，用于把本回合挂到既有 `session` 上；不传时才新建 `session` |
| `node_id` | integer | 是 | 当前节点 ID |
| `option_key` | string | 否 | 所选选项 `A`/`B`/`C`；走自定义决策时不传 |
| `input_text` | string | 否 | 自定义决策文本 |
| `duration_ms` | integer | **是** | 本回合决策耗时（毫秒），**必须 `>= 0`**；写入 `turn.duration_ms`（该列非空） |

> **`attempt_no` 由服务端计算，客户端不得传入**：一次 attempt 归属 `(case_id, student_token, student_name)`；同一 session 内刷新沿用当前 attempt，学生显式“再试一次”时服务端分配新的 `attempt_no`，避免客户端自行拆分尝试记录。

- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | integer | 本次推演 ID（前端存 sessionStorage，供 S3 / S4 使用） |
| `result` | object | 固定为 `{before_metrics, after_metrics, delta_metrics, summary, source, warning?}`，见下表 |
| `next_node_id` | integer \| null | 按 `idx` 取的下一个节点 ID；**已是最后一个节点时为 `null`** |

`result` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `before_metrics` | object | **选择前**的指标状态（四键），即上一回合的 `after_metrics`；首回合为 `case.base_metrics` |
| `after_metrics` | object | **选择后**的指标状态（四键），等于所选选项的 `metrics`（目标状态快照） |
| `delta_metrics` | object | 固定四键：`revenue` 为数值差；`gross_margin` 与 `market_share` 为解析百分比后的百分点差数值；`cash_flow` 为 `{from, to}`，其中 `from` / `to` 均为字符串 |
| `summary` | string | 所选选项的结果摘要 |
| `source` | string | `preset_option` / `custom_input` |
| `warning` | string \| null | 仅在结果需要提示时出现，例如自定义决策「未套用预设测算」；正常情况不出现 |

`result.delta_metrics` 的固定形态为：`{revenue: number, gross_margin: number, market_share: number, cash_flow: {from: string, to: string}}`。其中百分比先解析再计算百分点差，`cash_flow` 只表达文字方向变化。

- 语义：
  - 写一条 `turn`：`before_metrics_json` / `after_metrics_json` / `delta_metrics_json` / `result_source`，
    以及兼容字段 `result_json = {metrics: after_metrics, summary}`；
  - 同时把 `session.current_metrics_json` 更新为本次的 `after_metrics`（见 §1.5 指标状态机）；
  - `next_node_id` 为 `null` 时（三个节点已走完），写 `session.finished_at` 并自动生成复盘 —— 详见 §1.5 与 §1.8；
  - **`option_key` 与 `input_text` 至少有一个**；只给 `input_text` 时 `chosen_option` 记 `""`、
    `result_source` 记 `custom_input`、`after_metrics` 沿用 `before_metrics`（自定义决策没有预设测算），
    并在 `warning` 里说明。
- 失败：`404 {"detail": "推演链接无效"}` / `404 {"detail": "节点不存在"}` / `400 {"detail": "请选择选项或填写自定义决策"}` / `400 {"detail": "duration_ms 必须是非负整数"}`

---

### S3 `POST /api/play/{student_token}/chat`（SSE 流式）

- 路径参数：`student_token`，string
- 请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | integer | 是 | 推演 ID，用于拼上下文 |
| `message` | string | 是 | 学生提问正文 |

- 响应体：`Content-Type: text/event-stream`，`StreamingResponse`。每条事件为 `data:` + 一行 JSON，行分隔为 `\n\n`：

| 事件 | 数据体 | 说明 |
|---|---|---|
| 增量 | `{"delta": "<文本片段>"}` | 可重复下发多次，前端逐字追加到当前气泡 |
| 结束 | `{"done": true}` | 流正常结束标志 |

- 语义：把该 `session` 已有的 `turn` 记录拼成上下文一起发给模型，逐块转发 `delta`；模型完整回答与本次提问分别落 `message` 表（`role` 为 `assistant` / `user`）。
- 前端必须处理流中断：中断时提示「连接中断，请重试」。

---

### S4 `GET /api/play/{student_token}/review?session_id=`

> 由原 `summary` 改名而来，响应结构整体替换（**本次修订**）。

- 路径参数：`student_token`，string；查询参数：`session_id`，integer（必填）
- 请求体：无
- 响应体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `framework_type` | string | 复盘所用框架，枚举：`SWOT分析模型` / `4P营销理论` / `盈亏平衡分析`；由案例 `case_type` 决定 |
| `dimensions` | array | 元素 `{name, content}`，商科组确认前按原文 SWOT 5 / 4P 5 / 盈亏平衡 4 传输，包含「综合结论」项；是否拆出待确认，**不得自行拆分为“4+1 / 4+1 / 3+1”** |
| `conclusion` | string | 兼容字段，回显综合结论文本；确认前不作为额外页面块 |

`dimensions` 元素字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 维度名称，必须是该框架模板里的原词（如 `产品（Product）`、`优势（S）`、`成本端`） |
| `content` | string | 该维度下结合学生本次决策的分析正文 |

> 商科组确认前，前端和 API 均按原文 SWOT 5 / 4P 5 / 盈亏平衡 4 渲染与传输；是否将「综合结论」从 `dimensions` 拆出，待商科组确认后再决定。不得将其解释或拆分为“4+1 / 4+1 / 3+1”。

- 语义：按 `turn` 还原该次尝试的决策路径喂给模型，按模板维度生成正文；结果落 `review` 表（`session_id` + `attempt_no`），已存在则直接返回历史结果、不重复调用模型。
- 校验：生成后按 `validator` 的 V5 逐项核对维度名称与顺序，不通过则重新生成最多 2 次。
- 失败：`404 {"detail": "推演记录不存在"}` / `400 {"detail": "推演尚未完成"}`（`session.finished_at` 为 `null` 时）

---

### S5 `POST /api/play/{student_token}/try-case`

> 本节的契约**以当前代码为准**（`schemas.TryCaseEcho`），供学生自己生成内容用；
> 后续步骤会把它扩展为可交互的试跑，届时再改本节。

- 路径参数：`student_token`，string（沿用 student_token 鉴权，不新增权限体系）
- 请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string | 是 | 学生自己贴的案例素材或主题关键词；空则 `400`。短于 30 字按主题关键词处理 |
| `title` | string | 否 | 自拟标题；不传则由模型生成 |
| `case_type` | string | 是 | 指定框架类型，枚举同 §1.2 的 `case_type` 三选一 |

- 响应体（`TryCaseEcho`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `try_id` | string | `uuid4().hex`，32 位十六进制 |
| `case_type` | string | 回显案例类型 |
| `background` | string | 生成的企业背景 |
| `base_metrics` | object \| null | 四项基准数据 |
| `nodes` | array | 试跑节点，见下表 |

`nodes` 元素字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 试跑不落库，**用 `idx` 代替主键** |
| `idx` | integer | 顺序号 1/2/3 |
| `node_role` | string | `核心战略` / `核心策略` / `落地执行` |
| `title` | string | 节点标题 |
| `background` | string | 节点背景说明 |
| `options` | array | 元素 `{key, label, risk_level, summary, metrics}` |

- **保密口径**：S5 / S6 返回的是学生自己贴素材生成的 `TryCaseEcho`，可返回自己的 `risk_level` / `summary` / `metrics`；这不涉及教师案例答案。唯一的提前保密约束是 S1，S2 可返回本次已选结果。
- **存储**：结果只放**进程内内存**（`TRY_STORE`，上限 50 条，超出按创建时间淘汰最旧）。
  **不写 case / node / option_result / session / turn / review 任何一张表**，也不生成 student_token 与二维码。
  服务进程重启后所有 `try_id` 失效，属预期行为。
- **校验**：生成时复用 `generate_case`，并过 `validator` 的 **V2 与 V3**（外加结构兜底），不通过则重生成最多 2 次。
- 失败：`400 {"detail": "请提供案例素材或主题关键词"}`；生成失败 `503 {"detail": "试跑生成失败，请稍后重试：…"}`。

---

### S6 `GET /api/play/{student_token}/try/{try_id}`

- 路径参数：`student_token`、`try_id`，均 string
- 请求体：无
- 响应体：与 S5 相同的 `TryCaseEcho` 结构（`try_id` 回显）
- 语义：从内存回读一次试跑结果（前端刷新页面或重新进入时拉取）。
- 失败：`404 {"detail": "试跑内容已失效，请重新生成"}` —— 含三种情况：从未生成、已被容量淘汰、服务进程重启。

---

## 附录 A · 无待确认项

1. **`session.finished_at` 的写入时机**：基线原文未写明。本次修订已按「`decide` 响应中 `next_node_id` 为 `null`（三个节点走完）时写入」落到 §1.5 与 S2 语义，若与预期不符请指出。

> 以上约定已在本次修订中解决：① `GET /api/cases` 补上 `?token=`（见 T3）；② `attempt_no` 不在 `decide` 请求体中，由服务端按 attempt 归属规则计算（见 S2）。
> node_role / title / background 的下发出口已在本次修订中补齐（T4 与 S1 的 nodes 元素）。
> session.finished_at 的写入时机已确认：decide 响应中 next_node_id 为 null（三个节点走完）时写入，见 §1.5 与 S2。
> 代码与文档反向同步记录：T5/T6 原写的「已发布后 403」已按商科规范 7.2 删除；T6 请求体按实际实现补为超集。
