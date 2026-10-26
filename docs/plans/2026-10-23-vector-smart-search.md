# 向量标注接入智搜

**Goal:** 在文搜图 / 图搜图标注页直接搜索外部智搜平台，自动切图并沿用匹配标注、审核、导出。

**Architecture:** 保留本地向量检索，增加智搜来源。复用平台登录与现有 MatchQuery；平台账号留在后端。结果原图存项目私有存储，按归一化框生成私有裁剪 Asset。图搜图从已保存结果打开原图框选，后端将像素坐标及原图 URL 提交 /search/cut；查询参考图也固定保存，供审核与导出使用。

**Tech Stack:** FastAPI / SQLAlchemy / Pillow / requests / React / Ant Design。

## 已确认的范围与假设

- 用户确认：从智搜结果选图并框选；在结果卡片直接判定正确 / 错误。
- 复用启用的平台列表；单次选择一个平台，默认今天及 20 条，数量上限 100。
- 文搜图使用 /api/board/aiNvr/search；图搜图使用 /api/board/aiNvr/search/cut。
- 实测杭州移动消防两接口均返回 data 列表；x1/y1/x2/y2 为归一化坐标。cut 请求为原图像素坐标，默认返回 15 条。
- 单张图片网络、解码或框异常不影响其他结果；失败数与原因返回页面。只有成功裁剪图进入匹配历史，默认未判定。
- 保留原图 URL、设备时间、原始尺寸与框坐标。查询历史与数据归属当前项目，后台校验项目 / 租户权限。
- 不自动生成向量、不写 Milvus、不改旧下载任务，不给业务数据自动打正确标签。

## 决策

直接复用 MatchQuery 与现有结果卡片，不另建标注模块。裁剪图保持项目私有；查询参考图使用 query_asset_id，避免将项目裁剪图暴露到共享图片库。仅存原图文件与来源，不将原图混入标注队列。

## 实施与验证

1. 新增 tests/test_vector_smart_search.py：先失败，覆盖接口原始记录 / cut 参数、归一化切图、坏图与坏框、权限、重复查询去重、标注历史、图搜图参考图 / 导出。
2. 修改 integrations/smarteye/api/smart_search.py：允许调用 cut 并保留 records，保持旧 images 返回兼容。
3. 新增 schemas/vector_smart_search.py 与 services/vector_smart_search.py，扩展 api/project_work_routes.py；私有切图与原图读取均在当前项目授权范围内。
4. 修改 match_review.py、multimodal_validation.py、annotation_export.py：支持项目内查询参考图。
5. 扩展 frontend/src/api/projectWork.ts、VectorAnnotation.tsx，新增框选弹窗，复用 DetectionCanvas。
6. pytest 隔离数据库 / 图片存储通过，前端构建通过，浏览器拦截 API 验证文本搜索、卡片标注、框选及图搜图请求。真实平台仅做不入库的只读接口实测。
