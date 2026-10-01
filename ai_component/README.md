# AI 维护区

你主要修改这里：providers/llm.py 管模型协议、预算、重试；agents/ 管案例生成/修复、独立复盘审核、答疑规划和教师建议；prompts/ 管案例提示词构造。其余任务提示词与各自 Agent 共置，均不散落到业务路由。

公开入口见 ../contracts/README.md。不要导入 backend、SQLAlchemy、FastAPI；不要自行加载 .env 或打开学生文件。日志通过 contracts.telemetry 输出，宿主沿用已有可见日志窗口。

模型配置依旧在 backend/.env，由平台启动加载。模型名不写死，密钥不进前端。只改模型或提示词通常不需要搭档改路由和表结构；输出约定变化需双方联调。

在项目根运行：

    .\.venv\Scripts\python.exe -m pytest ai_component/tests -q

这些核心测试以纯数据、内存工具和 Mock 模型运行，不导入平台和真实数据库，不产生模型费用。协议兼容/超时/日志和数据库工作流联调仍位于 backend/tests。
