# SmartEye 内置接口

台账下载已同步原项目 `851364d`：每轮先查询所选平台 MySQL 的 `aisf_alarm_analysis`（开始时间包含、结束时间不包含，按预警类型筛选），再按设备和预警时间前后各15秒查询台账。每条预警选择最近的原图，同距离优先 `backPath`；台账检测结果只辅助裁图/目录命名，不二次排除数据库已选预警。结果只在本轮内存使用，不新增预警缓存表。

预警数据库连接配置放在私有 `backend/backend/.env.smarteye` 的 `LEDGER_ALARM_DATABASES` JSON 中，以平台ID为键，值包含 `host`、`port`、`user`、`password`、`database`；也可用进程环境变量 `SMARTEYE_LEDGER_ALARM_DATABASES` 覆盖。已同步原项目的绍兴/嘉兴配置，其他平台未配置时明确失败，不回退到全量设备扫描。不要将配置值提交或输出到日志。

定时任务的查询范围等于执行间隔；监听任务按轮询间隔查预警，台账偏移固定15秒。单轮耗时从等待间隔扣除；若耗时超过间隔，沿用原项目立即执行下一轮的行为，可能有查询空档。每条预警分别计入成功、未匹配或失败；缺少台账/原图分别统计。同名文件串行处理，成功次数包含重复预警，最终目录文件数和相似图去重保留数独立展示。共享入库、任务持久化、取消/重试及项目关联仍由主平台执行。

所有下列路径均运行于主后端8000，要求主平台Bearer Token，不再使用独立5000端口。第一版限定主租户；同租户资源共享。原Flask返回体保留code/data/message等原约定，主认证错误遵循FastAPI约定。

下载、AI素材/生图、工具、监控、调度配置等创建请求可带 `X-SmartEye-Purpose: download`（默认）或 `annotation`。选择annotation时必须同时带 `X-SmartEye-Project: <项目UUID>`，成功图片直接登记资产。重试保留原用途和项目；读取、停止不要求重新选择项目。

统一任务入口：GET `/tasks`和`/tasks/<task_id>`，POST `/tasks/<task_id>/cancel`和`/tasks/<task_id>/retry`（均加上完整前缀）。仅检测/台账/智搜/工具任务支持通用手动重试；AI等需从对应页面重新提交。

主OpenAPI显示桥接通配路由，下表从本轮实际Flask路由注册表生成，存在不等于真实子平台已验收。参数继续由原六组页面及原处理函数定义。

| 方法 | 路径 |
|---|---|
| GET | `/api/smarteye/ai-image/assets/<task_id>/<path:relative_name>` |
| GET | `/api/smarteye/ai-image/history` |
| GET | `/api/smarteye/ai-image/history/<task_id>` |
| POST | `/api/smarteye/ai-image/import-existing` |
| POST | `/api/smarteye/ai-image/import-urls` |
| GET | `/api/smarteye/ai-image/progress/<task_id>` |
| GET | `/api/smarteye/ai-image/reuse/<task_id>` |
| GET | `/api/smarteye/ai-image/reuse/<task_id>/<item_id>` |
| POST | `/api/smarteye/ai-image/start` |
| POST | `/api/smarteye/ai-image/upload` |
| POST | `/api/smarteye/ai-image/upload-folder` |
| GET | `/api/smarteye/alarmType` |
| POST | `/api/smarteye/download` |
| POST | `/api/smarteye/download-images` |
| GET | `/api/smarteye/download-zip/<task_id>` |
| GET | `/api/smarteye/ledger-image/download/<task_id>` |
| GET | `/api/smarteye/ledger-image/progress/<task_id>` |
| DELETE | `/api/smarteye/ledger-image/scheduler/<sched_id>` |
| POST | `/api/smarteye/ledger-image/scheduler/configure` |
| POST | `/api/smarteye/ledger-image/scheduler/start` |
| GET | `/api/smarteye/ledger-image/scheduler/status` |
| POST | `/api/smarteye/ledger-image/scheduler/stop` |
| POST | `/api/smarteye/ledger-image/start` |
| POST | `/api/smarteye/ledger-image/stop/<task_id>` |
| POST | `/api/smarteye/ledger-image/update-devices` |
| GET | `/api/smarteye/ledger-watch/jobs` |
| POST | `/api/smarteye/ledger-watch/jobs` |
| PUT | `/api/smarteye/ledger-watch/jobs/<job_id>` |
| DELETE | `/api/smarteye/ledger-watch/jobs/<job_id>` |
| POST | `/api/smarteye/ledger-watch/jobs/<job_id>/start` |
| POST | `/api/smarteye/ledger-watch/jobs/<job_id>/stop` |
| POST | `/api/smarteye/ledger/device/log` |
| GET | `/api/smarteye/platform-list` |
| GET, POST | `/api/smarteye/platforms` |
| GET | `/api/smarteye/progress/<task_id>` |
| GET | `/api/smarteye/smart-search/download-zip/<task_id>` |
| GET | `/api/smarteye/smart-search/platforms` |
| GET | `/api/smarteye/smart-search/progress/<task_id>` |
| POST | `/api/smarteye/smart-search/start` |
| POST | `/api/smarteye/smart-search/sync-platforms` |
| GET | `/api/smarteye/tasks` |
| GET | `/api/smarteye/tasks/<task_id>` |
| POST | `/api/smarteye/tasks/<task_id>/cancel` |
| POST | `/api/smarteye/tasks/<task_id>/retry` |
| GET | `/api/smarteye/tool-tasks` |
| GET | `/api/smarteye/tool-tasks/<task_id>` |
| GET | `/api/smarteye/tool-tasks/<task_id>/download` |
| POST | `/api/smarteye/tool-tasks/create` |

[运行限制](../../backend/backend/integrations/smarteye/README.md) · [验收证据](../testing/p1-verification-2026-10-13.json)
