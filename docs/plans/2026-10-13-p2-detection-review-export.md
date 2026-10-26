# P2 Detection, Review and Export Implementation Plan

**Goal:** 在test-img项目中完成“人”的检测画框、审核、历史回读及可训练图片包导出。

**Architecture:** 保留主React/FastAPI与统一图片存储；标注更新追加版本，以资产行锁和base_version拒绝旧写入，审核与批量操作共用状态/计数事务。训练包只使用最新标注，记录版本/类别/哈希与筛选范围，冻结包独立保存，不受后续编辑影响。

**Tech Stack:** React/TypeScript、SVG与Pointer Events、Ant Design、FastAPI、SQLAlchemy、PostgreSQL、Pillow、ZIP。

1. tests/test_p2_annotations.py先复现越界框、多latest、旧写入、历史审核及错误计数；services/annotations.py、annotation_routes.py、schemas/annotation.py、batch_routes.py、asset_routes.py共用锁/校验/版本/审核/计数。models/annotation.py及007迁移增加唯一约束与业务事件/冻结导出表。验证失败测试变绿，PG并发只一项成功，旧数据迁移保留。
2. tests/test_p2_exports.py先解析多框/负样本/部分标签/同名图片/权限/冻结回读与划分；export_routes.py输出YOLO/COCO ZIP及manifest；显式冻结训练/验证/独立测试划分，拒绝重复图跨划分。旧JSON/CSV保留，COCO按boxes映射纠错。验证图片哈希、每个框、类别ID与YOLO归一化。
3. frontend/src/pages/DetectionWorkspace.tsx复用资产/项目/认证图片API，SVG画框/选择/移动/尺寸坐标编辑/删除，类别默认人，负样本显式确认、保存/重开/历史/审核。AssetsPage、AnnotationsPage、App.tsx补入口；项目导出提供类别/审核范围、格式与冻结包下载。验证构建、浏览器实际拖框/编辑/保存/冲突提示/审核/导出。
4. 实际test-img六张图片保留。仅在用户指定图片上做可回看的人工联调，不把联调框当场景全量标注验收；自动测试用独立库。迁移前备份并核对哈希，运行后记录事件/版本/计数及导出校验。
5. 全量P0/P1/P2回归、前端构建与浏览器操作，更新TODO/接手记录和证据。设备/质量分布以现有元数据为限；近重复/切图泄漏、真实训练加载器、自动模型预标注另列未验收，不用空响应替代验收。

不自动提交推送，不改原SmartEye目录，不调用付费模型，不改变已有图片原图。
