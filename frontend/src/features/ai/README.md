# 前端 AI 功能区

搭档维护页面交互，与你共同确认输出展示：

- StudentTutorSidebar：当前案例/本人会话选择、答疑消息与可读引用。
- StudentReview：动态复盘、后台等待、断线重连、重试；token/sid/onRestart 由业务页面提供。
- ReviewEditor：教师校准，最终写入业务复盘记录。
- GeneratePanel / AiFixDrawer：素材受理界面与教师建议。
- TaskStatus：AI 任务状态文案与展示。
- api.ts：AI HTTP/SSE 请求；复用 ../../api/transport.ts 的同源传输与身份配置。
- ai.css：生成和校准专属样式。

登录、路由、案例列表、确定性推演、记录分组、发布仍在 pages/ 和 layouts/。不要在此配置 DeepSeek 地址或 Key。HTTP 类型仍集中在 api/types.ts，避免两套定义漂移。
