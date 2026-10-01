# AI / 平台 interface · v1

这是双方共同维护的 seam，不是独立服务器。根目录下运行 Python，无需安装独立 AI 服务。

## 数据与能力

- ai.py：TeachingPolicy 为平台规则的数据副本；ReviewInput 含冻结证据、框架名称和基础分析维度。AI 不接收 ORM 实例。
- TutorTools：宿主绑定当前已授权会话，read(names) 只读允许的视图；search(query) 由宿主过滤隐私、限制来源；record(data) 保存可追溯原始引用。模型不能传入 SQL、文件路径、学生 ID 或案例 ID。
- JsonModel：异步 JSON 模型调用约定；公开 AI 入口接受可选 model，默认兼容协议提供器，测试可注入离线模型。
- errors.py：LLMFailed.retryable 表示临时网络/服务错误是否允许重试；ValidationExhausted 表示有限修复已耗尽，不重跑整个工作流。
- review.py：动态分析项的共同完整性校验，不补项、不重排、不裁剪；基础内容与非空独立结论必需。
- presentation.py：内部来源 ID 转为可读中文；原始引用只用于服务器追溯。
- telemetry.py：事件、关联 ID 和脱敏约定；只有平台安装日志处理器。

CONTRACT_VERSION=1。修改必需字段、错误语义、证据范围须双方确认并更新测试；可选新增字段不应破坏 v1 调用方。

## 公开 AI 入口

从 ai_component 导入：

1. generate_valid_case(source_text, source_kind, case_type, framework, policy=..., validate=..., model=...)
   返回 (候选稿, issues)，失败可能返回 (None, issues) 或抛 LLMFailed。framework 只传含 prompt_template 的字典；validate 由宿主提供确定性商科校验。
2. StudentReviewAgent(model=...).run(ReviewInput(...), record)
   返回 framework_type、动态 dimensions、conclusion；record(stage, data) 由宿主存制品与任务阶段。
3. TutorAgent(model=...).run(question, tools)
   返回可读引用的答案，或教学范围拒答文本；权限过滤不能交给模型。
4. suggest_fix(context, question, model=...)
   返回 reply 建议文本，不修改案例。

当前 legacy 自助试跑由平台 trial Adapter 使用 generator 的内部候选入口；它不是正式课堂决策链路。以后调整此入口需连同 trial Adapter 测试更新。

## HTTP 兼容性

现有 /api/cases、/api/ai-tasks、/api/play、/api/student、/api/teacher URL 不变。身份和令牌仍由平台负责。任务状态 queued/running/succeeded/failed；学生复盘生成中为 pending/running，完成为 succeeded，失败为 failed。

HTTP DTO 的权威定义仍是 backend/app/schemas.py；前端静态类型在 frontend/src/api/types.ts。内部 Python interface 不等同于 HTTP DTO，不复制一份新的 HTTP schema；联调测试验证两侧兼容。
