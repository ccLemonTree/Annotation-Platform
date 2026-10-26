# Multimodal Annotation Implementation Plan

**Goal:** 项目内完成单选/多标签分类、描述、Milvus 检索配对标注，并为所有项目提供检测裁剪。

**Architecture:** 复用 Asset、Annotation 版本/审核/计数、图片库、模型快照与推理助手。新增持久项目处理队列和查询配对记录，向量写入平台专用 Milvus collection；原图与派生图分开，保存来源及框。项目工作台集中生成、检索和标注；旧图片描述检索入口跳转到标注项目，历史 API 保留。

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL, httpx Milvus REST v2, React/Ant Design.

---

### 1. Annotation contract and project types
- Modify backend/backend/{models/project.py,schemas/project.py,services/annotations.py,services/project_labels.py}.
- Test classification single/multi validation, caption validation, stale versions, review/counters, schema changes that invalidate existing annotations.
- Add caption/vector enum values through additive migration 021; retain all existing records.

### 2. Milvus and durable project processing
- Create models/project_work.py, schemas/project_work.py, services/milvus.py, services/project_work.py, api/project_work_routes.py; wire startup/router/model registry.
- Unit and isolated DB tests first: tenant scope, compatible enabled detection models, crop boundary rounding/expansion, no detection is not a negative, duplicate/retry/cancel fencing, immutable model snapshots, stale annotations, original provenance.
- Use one serial worker; crop/index/caption are separate jobs. Users first select originals and crop, then select resulting crops for annotation/indexing. No original removal or automatic labels from detection scores.
- Dedicated `image_platform_annotations_v1` collection, VARCHAR deterministic IDs, COSINE/2048, Strong search. PostgreSQL remains authority for project membership/current content; rejected/cancelled writes never become visible search candidates.
- Query records contain immutable query/space and returned candidates; judgment is versioned per query and only applies to returned candidate. Unknown stays out of positive/negative exports.

### 3. Default exports
- Create services/annotation_export.py and extend project export route.
- ZIP contains images, annotations.jsonl, manifest. Single classification additionally class directories by numeric category id; multi-label uses JSONL. Caption includes text; vector adds queries.jsonl with positives/negatives/unknown and model snapshot. Include source grouping and content/version/hash. Default approved-only, explicit include-pending option.
- Test archive contents, scope, approved filters and unsafe names.

### 4. UI integration
- Create project annotation tools (jobs, model selector/prompt/target/confidence/expansion, retrieval cards and judgments) and non-detection editor; use existing /annotate/:assetId route dispatcher.
- Extend ProjectsPage type/config and project schema editing, AnnotationsPage tools, AssetsPage project tools entry/export.
- Old image-semantics entry redirects to annotation page, retain legacy API/history.
- Browser check classification, caption editing, text/image queries, correct/incorrect/unknown, crop model picker (all enabled detectors), jobs/retry/cancel, export, narrow screen and no overflow.

### 5. Verification and activation
- Run focused red/green tests, full backend regression, frontend build/lint.
- Verify 020 -> 021 with a disposable PostgreSQL database; back up live database before additive activation; preserve training jobs/server files. Live Milvus smoke uses only a clearly owned temporary collection, then removes that test collection.
- Record proved behavior and any external model limits. No commit or deployment of remote workloads.
