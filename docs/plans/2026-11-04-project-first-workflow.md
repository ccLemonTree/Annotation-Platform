# 项目数据与标注流程 Implementation Plan

**Goal:** 在项目内完成采集、复用、预标注、审核和逐轮切图，保留既有数据。

**Architecture:** 复用现有项目图片、后台任务与标注版本表；原图仍按内容哈希存储。用最新审核状态控制共享和新建跨项目引用，不迁移历史引用。每轮定时抓图仅查询最新成功记录。

**Tech Stack:** React、Ant Design、FastAPI、SQLAlchemy、PostgreSQL，SQLite隔离测试。

1. `ProjectsPage.tsx`、`ModelRepositoryPage.tsx` 改卡片；保留分页、版本选择与操作。`DeviceCapturePage.tsx` 修正场景弹窗展示鉴权大图。验证：前端构建与隔离浏览器。
2. `ProjectWorkflow.tsx`、`MainLayout.tsx`、`App.tsx` 收拢项目操作入口，复用已有工作区。验证：固定项目参数与导航。
3. `image_library.py`、`image_routes.py`、SmartEye `runtime.py` 统一共享判定；锁原图后判断是否可绑定新项目，既有引用幂等。验证：未审核拒绝、审核后允许、最新版本失效、历史保留。
4. `device_capture.py` 每轮只取最新成功一张。验证：大量上游记录也只请求第一页一条，空结果、失败与取消保持原语义。
5. `project_work.py` 保存完整裁剪链与根图坐标；准备区按轮次选择。验证：人→脸→五官三轮来源、坐标、独立标注与无共享图库污染。
6. `annotation_analytics.py` 用只读运行证据定位慢路径并优化。验证：原有统计回归与复杂度检查。
7. 本地通过后，通过 ssh-manager 核对115差异与活动任务，备份本轮文件再更新；有活动任务则不重启后端。验收接口、构建和实际页面。

用户确认：保留已有跨项目引用；同项目多轮切图并保留完整来源。抓图最新要求：只抓最新一张，取消历史窗口补拉。
