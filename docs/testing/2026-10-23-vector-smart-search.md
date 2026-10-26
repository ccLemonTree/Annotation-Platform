# 智搜接入向量标注验证记录

## 实现

- 向量标注增加“智搜平台”来源，保留本地向量索引检索。
- 文搜图调用 /search，按返回的归一化框裁剪；卡片只展示项目私有目标切图，可直接判定并自动保存。
- 卡片“用此图搜图”打开已保存原图，复用 DetectionCanvas 选择区域；/search/cut 接收原图像素坐标。参考切图固定保存到当前项目，审核、导出和验证集可读取。
- 图片下载 / 解码 / 框坐标异常逐条隔离；失败原因随查询历史保存。原图不混入标注列表，账号不返回前端。

## 已执行验证

- 隔离后端回归：`pytest tests/test_vector_smart_search.py tests/test_project_work.py tests/test_match_review.py tests/test_match_ai_review.py tests/test_swift_export_api.py tests/test_smarteye_integration.py tests/test_multimodal_validation.py -q`：96 passed（随后新增独立导入测试，智搜单文件 8 passed）。仅有既有弃用警告。
- 前端 `npm run build` 通过；有既有分包体积警告。
- Playwright `scripts/verify_vector_smart_search.cjs` 通过：文搜图请求、切图卡片直接判定、失败提示、原图框选到像素坐标、图搜图请求与模式切换、键盘判定、390px 无横向溢出。全部 API 被隔离 fixture 拦截，业务写入 0。
- 截图：output/playwright/vector-smart-{text,crop,mobile}.png。
- 杭州移动消防平台真实只读实测：文搜“人”返回 1 条，使用新下载 / 切图逻辑得到 280×294 的目标图；新 cut 适配返回 15 条。只在内存读取图片，业务写入 0。
- 后端变更文件 py_compile 通过。

## 生效情况

最初尝试停止并重启原后端被自动审批拒绝，返回 `blocked by policy`，命令未执行。用户随后明确授权重启；再次核查时后端已由新进程 56048 于上海时间 2026-10-23 18:52:26 启动，因此未重复重启。当前 OpenAPI 已包含平台列表、智搜查询与原图读取三个新接口；健康检查及认证后的平台列表返回 200（68 个启用平台，包含杭州移动消防平台）。未对真实业务项目做搜索入库测试。
