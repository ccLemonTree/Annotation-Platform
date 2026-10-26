# 文档目录

先读[Codex接手记录](development/CODEX_HANDOFF.md)，后续开发按[当前TODO](development/TODO.md)的P0—P5顺序及验收条件执行，再按需要查看下表。用户最近确认的决定优先于旧文档；第一版单租户、SmartEye完整内置、图片共享存储的方向已确定。

| 分类 | 文档 | 使用说明 |
|---|---|---|
| 操作与接口 | [训练效果分析与历史对比](api/训练效果分析.md) | 训练详情查看指标、曲线、大模型分析、历史对比及持久化报告 |
| 操作与验收 | [YOLO标注导入](development/文件夹与压缩包上传.md#yolo已标注图片导入2026-10-17) · [训练产物与任务管理](development/模型训练.md#下载模型产物与删除任务) · [验收](testing/import-training-verification-2026-10-17.json) | ZIP标注、管理员信任、产物下载、仅删记录及日志展示 |
| 操作与验收 | [Qwen提示词编辑](development/模型服务管理.md#qwen-提示词编辑2026-10-16) · [验收](testing/qwen-prompts-verification-2026-10-16.json) | 模型描述/审核核心指令、图片集场景指令、单批覆盖与019迁移 |
| 操作与验收 | [GPU服务器SSH调试](development/GPU服务器SSH调试.md) | 管理员交互终端、MCP扩展、会话边界及真实A100验证 |
| 开发与验收 | [GPU服务器连接计划](plans/2026-10-16-gpu-server-connections.md) · [验收](testing/gpu-server-connections-verification-2026-10-16.json) | IP/密码连接、加密存储、MCP动态连接、017迁移及真实页面 |
| 操作与接口 | [模型验证集](development/模型验证集.md) · [接口](api/模型验证接口.md) | 人审真值冻结、模型评估、逐图错误、覆盖率与报告 |
| 开发与验收 | [验证集计划](plans/2026-10-16-model-validation.md) · [验收](testing/model-validation-verification-2026-10-16.json) | 016增量、独立AP对照、PG取消/重试及真实页面保护 |
| 操作与接口 | [智能处理与审核](development/智能处理与审核.md) · [接口](api/智能任务接口.md) | 预标注/负样本/管理员队列、描述检索、质量效率与预览快捷键 |
| 测试证据 | [P3智能处理验收](testing/p3-automation-semantics-analytics-2026-10-15.json) | 015增量、362项回归、真实模型与页面及未完成边界 |
| 操作与接口 | [GPU资源管理](development/GPU资源管理.md) | 绍兴A100只读采集、管理员权限、刷新和部署配置 |
| 测试证据 | [GPU资源验收](testing/gpu-resources-verification-2026-10-15.json) | 真实MCP与平台API、页面回归及边界 |
| 测试证据 | [P0、数据集和模型整合验收](testing/foundation-datasets-models-verification-2026-10-15.json) | 014迁移保护、全回归、实际模型调用与待完成边界 |
| 操作与接口 | [模型服务管理](development/模型服务管理.md) | 模型配置、接口协议、真实服务、预测预览和类别映射 |
| 测试证据 | [数据集划分与来源分组](testing/p2-04-dataset-splits.md) | 来源编辑、分组建议、预算/阈值、冻结证据与兼容边界 |
| 操作与接口 | [存储核查与离线恢复](storage-cleanup-p0.md) | 只读预览、在线拒绝、离线隔离归档/恢复及连接恢复 |
| 测试证据 | [数据库收尾](testing/p0-database-closeout-2026-10-15.md) | 013训练表、空库初始化、014模型配置和拒绝破坏性修复 |
| 测试证据 | [继续标注、序号与负样本](testing/workspace-progress-verification-2026-10-15.json) | 四个继续入口、回车跳转、N清框与撤销、失败/历史保护 |
| 测试证据 | [标注切图修复](testing/workspace-navigation-verification-2026-10-15.json) | 稳定分页、直接定位、自动保存保护与真实4676图前后切换 |
| 测试证据 | [图片库与项目批量选图](testing/image-bulk-selection-verification-2026-10-15.json) | 跨页/筛选全部、1000张批次与失败重试、真实4676张只读选择 |
| 开发实施 | [批量选图计划](plans/2026-10-15-image-bulk-selection.md) | 两个入口共享筛选、项目排除已用、分页与表格布局 |
| 测试证据 | [默认图片集、合并与场景并发](testing/collection-merge-scenes-verification-2026-10-15.json) | 已有目标记忆、4676图合并保护、4路真实吞吐 |
| 操作与接口 | [文件夹与压缩包上传](development/文件夹与压缩包上传.md) | 图片/文件夹/ZIP/TAR、分片续传、限制与012升级 |
| 开发实施 | [分片上传计划](plans/2026-10-15-resumable-upload.md) | 持久会话、归档校验与原有入库复用 |
| 测试证据 | [分片上传验收](testing/resumable-upload-verification-2026-10-15.json) | 隔离PG/真实HTTP、浏览器拦截、运行接口与数据保护 |
| 测试证据 | [上传窗口关闭重开](testing/upload-dialog-lifecycle-verification-2026-10-15.json) | 窗口隐藏继续队列、规则/进度保留、刷新边界 |
| 开发状态 | [Codex接手记录](development/CODEX_HANDOFF.md) | 当前已确认范围、代码入口、实际检查结果和开发顺序 |
| 操作说明 | [训练模型下发](development/模型下发.md) | K8s/Docker/Kafka、传输校验、手动生效、旧版本恢复与验收边界 |
| 开发计划 | [当前TODO](development/TODO.md) | 原114项逐项核对、问题与测试证据，以及P0—P5开发清单 |
| 需求 | [需求梳理v0.3](requirements/数据管理平台_需求梳理_v0.3.md) | 长期需求；其中多租户、生产下发等内容不属于当前第一版范围 |
| 设计 | [实现方案v0.1](design/数据管理平台_实现方案_v0.1.md) | 总体实施参考，技术建议和未决事项需要随开发更新 |
| 设计 | [SmartEye集成架构](design/架构设计-SmartEye集成方案.md) | 早期设计参考；Vue及独立导入步骤等描述待按当前决定修订 |
| 设计 | [数据库详细设计v1.0](design/数据库详细设计_v1.0.md) | 设计草案；现库、模型及迁移尚需核对，不能直接据此迁移 |
| 接口 | [API接口规范v1.0](api/API接口规范_v1.0.yaml) | 设计契约草案，部分路径与实装不同；运行接口可查看后端`/openapi.json` |
| 测试 | [测试计划v1.0](testing/测试计划_v1.0.md) | 验收用例及目标，目标值不代表已经达成 |
| 开发实施 | [P0基础修复计划](plans/2026-10-13-p0-foundation.md) | 已执行范围、调用方和验证步骤 |
| 测试证据 | [P0实施结果](testing/p0-verification-2026-10-13.json) | 当前61项测试、运行服务/浏览器、备份迁移及明确限制 |
| 开发实施 | [P1 SmartEye计划](plans/2026-10-13-p1-smarteye-integration.md) | 正式源码内置、共享原图、持久状态与验证范围 |
| 运行与接口 | [SmartEye内置说明](../backend/backend/integrations/smarteye/README.md) · [接口清单](api/SmartEye内置接口.md) | 统一启动、认证、用途、恢复及部署限制 |
| 测试证据 | [P1实施结果](testing/p1-verification-2026-10-13.json) | 当前回归、浏览器、运行API及尚待真实平台验收的边界 |
| 开发实施 | [P2实施计划](plans/2026-10-13-p2-detection-review-export.md) | 检测框、版本/审核、训练图片包、冻结划分 |
| 运行与接口 | [检测标注与导出](api/检测标注与导出.md) | 工作台操作、base_version、类别/审核范围、快照及限制 |
| 测试证据 | [P2实施结果](testing/p2-verification-2026-10-13.json) | 回归、真实test-img、迁移、运行API及实际图片包验证 |
| 操作说明 | [独立图片库与项目使用](development/图片库与项目使用说明.md) | 上传／拉取、独立筛选、多项目使用、删除与历史保护 |
| 开发实施 | [标注与审核按项目](plans/2026-10-14-project-annotation-review.md) | 项目概览、项目内图片标注与工作台审核返回 |
| 测试证据 | [工作台窗口布局](testing/workspace-viewport-verification-2026-10-14.json) | 桌面整图/控件适应、侧栏滚动及68项浏览器回归 |
| 测试证据 | [标注与审核验收](testing/project-review-verification-2026-10-14.json) | 83项浏览器检查、只读API与检查期间数据更新边界 |
| 开发实施 | [图片集独立入库](plans/2026-10-14-collection-ingest.md) | 每集独立规则，同集去重、场景跟原图共享 |
| 测试证据 | [图片集配置验收](testing/collection-ingest-verification-2026-10-14.json) | 176项回归、011保护旧结果、运行与浏览器检查 |
| 开发实施 | [场景与分辨率入库](plans/2026-10-14-image-scenes-resolution.md) | Qwen后台场景分类、可配置尺寸门槛和失败重试 |
| 操作说明 | [场景与入库规则](development/图片场景与入库规则.md) | 两个入口共用设置、包含等号的长边门槛和历史分类 |
| 测试证据 | [场景与分辨率验收](testing/image-ingest-verification-2026-10-14.json) | 170项全回归、实际模型与数据保护、浏览器及明确限制 |
| 开发实施 | [项目与图片导航简化](plans/2026-10-14-clear-project-navigation.md) | 固定库／项目地址、当前项目抽屉选图及明确返回 |
| 测试证据 | [导航验收](testing/project-navigation-verification-2026-10-14.json) | 107项浏览器检查、5项编辑器测试、运行API及当前数据保护 |
| 开发实施 | [命名图片集](plans/2026-10-13-named-image-collections.md) | 日期或平台名分类、已有图加入及去重 |
| 测试证据 | [图片集验收](testing/image-collections-verification-2026-10-13.json) | 009迁移、平台分类与真实multipart上传检查 |
| 测试证据 | [图片库验收](testing/image-library-verification-2026-10-13.json) | 008回填、并发复用、数据保护和浏览器交互 |
| 操作说明 | [检测标注交互](development/检测标注操作说明.md) | 类别设置、缩放/平移、快捷键、重叠与撤销 |
| 测试证据 | [标注交互验收](testing/annotation-interactions-verification-2026-10-13.json) | 状态/几何、浏览器拦截及后端类别校验 |
| 开发实施 | [类别同步与自动保存](plans/2026-10-13-label-sync-autosave.md) | 明确映射、待审核新版本和切图保存边界 |
| 测试证据 | [类别同步与自动保存验收](testing/label-sync-autosave-verification-2026-10-13.json) | 并发回归、浏览器拦截与真实test-img同步 |
| 设计规范 | [统一界面](../frontend/DESIGN.md) | 主平台与SmartEye共用变量、组件及响应式规范 |
| 测试证据 | [UI验收](testing/ui-verification-2026-10-13.json) | 实际页面、弹窗、模拟任务交互和数据保护 |
| 测试证据 | [P2真实加载器](testing/p2-loader-verification-2026-10-13.json) | Ultralytics/COCO实读、YAML修复、新旧冻结包保护与130项回归 |
| 测试证据 | [TODO核对记录](testing/todo-audit-2026-10-13.json) | 2026-10-13修复前基线；耗时头判断已附更正说明 |
| 历史 | [归档说明](archive/README.md) | 旧版需求、重复README、完成报告、修复报告及早期指南 |

当前操作入口见[项目README](../README.md)、[主前端说明](../frontend/README.md)与[主后端说明](../backend/backend/README.md)。
