# 一个项目、两个维护区

采用同进程模块化架构：AI 实现在根目录 ai_component，平台后端在 backend、前端在 frontend；共享 interface 在 contracts，平台 Adapter 在 backend/app/integrations/ai。双方可分工修改，但统一部署，暂不引入两个仓库、网络服务或第二套数据库，以免给本地教学演示增加运维成本。

AI 不导入业务 ORM、路由、应用启动或存储配置，只接收数据与受限能力；平台拥有授权、任务恢复、制品落库、发布和教师最终修改。运行数据和配置位置不随源码移动，云端 PostgreSQL/OSS 迁移另行处理。
