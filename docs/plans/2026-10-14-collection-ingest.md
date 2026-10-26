# 图片集独立入库规则 Implementation Plan

**Goal:** 每个图片集独立配置分辨率和场景分类，上传/SmartEye/重试遵循该图片集规则；用户后续确认同集内容去重、场景结果随原图共享。

**Architecture:** 复用ImageIngestConfig及现有图片集，增加配置/版本；保留原图的场景状态和结果，增加待分类规则快照。平台配置仅为新图片集模板，已有集固定快照。重复图不重推理，手动重分类按选中集规则更新原图结果，所有所属集可见。011保留原图/标注/冻结及旧分类结果。

**Tech Stack:** FastAPI、SQLAlchemy/PostgreSQL、React/Ant Design，无新依赖。

1. 先写失败用例：两个图片集阈值不同、模型/场景不同、同图只分类一次且同集只一条成员、权限/精确重试、SmartEye目标集与按平台名规则；验证原实现不满足。
2. 修改models/image_collection.py、models/image.py、services/image_ingest.py、image_library.py、image_scenes.py及api/image_routes.py/asset_routes.py；011迁移回填图片集规则快照，保留原图结果。验证隔离SQLite/真实临时PG、迟到/配置版本/并发领取。
3. SmartEye bridge/runtime选择目标集或平台名，规则首次使用固定到任务，未分类原图写入共享图片队列；裁剪临时输入不分类。验证相同原图和取消保护。
4. 图片库新增空图片集入口，选中集后编辑设置/筛选/重试；上传读取目标集规则。SmartEye共用设置并可选集，未选按各平台图片集。浏览器写入拦截，无真实子平台触发。
5. 全回归/双入口构建后私有备份实际010库，升级011并核对原图/标注/冻结不变。仅重启本任务后端，更新操作说明/TODO/证据。单进程分类和EOS等既有边界保留，不提交推送。
