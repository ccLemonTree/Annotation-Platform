# 场景两级分类 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 配置、识别、人工修正及设备/图片/项目复用筛选支持“大类 → 具体场景”。

**Architecture:** 在既有 JSON 配置中增加分组，保留具体场景字符串和现有多标签结果，不迁移图片或标注。父级筛选在数据库分页之前展开为子场景；图片优先、无图按名称及人工优先规则保留。

**Tech Stack:** Pydantic、SQLAlchemy、Flask/FastAPI、React、Ant Design TreeSelect。

---

## 已确认与决策

- 用户确认使用交通、自然、生产、公共、居住商业、室内及摄像机位七类，可自行增删修改。
- 配置维护两级结构，一个设备仍可匹配多个具体场景；不强制单选大类。
- 采用分组元数据，具体标签仍保存原名称；不采用重写成路径标签的方案，保护旧结果与人工修正。
- 旧配置根据已有候选名称补充分组；全局默认配置单独备份后补入用户确认的分类模板，保留模型与其他规则。已有图片集配置保持独立。
- 父级筛选查询当前租户配置，保留先筛选后分页；不读取其他租户配置，不触发真实同步或分类。
- 暂不增加第三级、不引入依赖、数据库迁移、训练任务或完整历史失败存储。

## 实施与验证

1. schemas/image_ingest.py 与新增分组测试：校验大类名称、重复子场景、最大候选数量和旧配置兼容；先复现失败再实现。
2. services/image_scenes.py、integrations/smarteye/device_scenes.py：提示词携带大类信息，输出仍校验具体场景名单；验证图片/名称分类及人工修正。
3. services/image_library.py、api/image_routes.py、api/asset_routes.py、integrations/smarteye/management.py：公共父级筛选条件覆盖设备、图片、项目、选择全部与复用，测试分页及租户隔离。
4. 前端共享分组编辑器和 TreeSelect：两处设置编辑大类/子场景；设备、图片库、项目图片、复用选择器展示和筛选两级结构；保留历史标签。
5. 隔离后端测试、前端构建、模拟 API 页面验证。与本轮开始基线比较后，按最小差异合并115的已有独立修改，检查活动任务、备份后更新并只读验收。不提交其他会话代码，不重新同步或分类。
