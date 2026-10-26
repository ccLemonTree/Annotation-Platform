# 工作台标签筛选与 R 审核验证

检测标注工作台新增按最新已保存标注框的标签筛选；URL 保存 label，前后切图和按序号跳图使用相同队列。零匹配保留当前图片；未保存修改时禁止更改筛选。绘框标签选择保持独立。

R 复用当前图片审核通过接口，不自动切图。输入、弹窗、历史版本、未保存状态和非管理员负样本限制保持生效；进行中的审核不会重复提交，失败后提示并解除忙碌状态。

- 后端 `test_asset_navigation.py` / `test_annotation_scope.py` 共 5 项通过，覆盖最新版本、分页、全选、邻居及空结果。
- 浏览器脚本 `scripts/verify_workspace_label_review.cjs` 6 项通过，2 次模拟审核提交，真实标注写入 0。覆盖筛选与刷新、序号跳转、零匹配、R 键、防重复、输入/弹窗/历史/未保存限制、负样本权限和审核冲突恢复。
- 2026-10-20 前端生产构建通过。
- 在不启动应用后台 worker 的独立 ASGI 实例上，对真实 PostgreSQL 做只读接口验证。项目“验证项目”共 3945 张，fire 匹配 1395 张、smoke 646 张、star 0 张；列表、选择、邻居与独立逐标注计算结果一致。脚本 `output/verify-workspace-label-live.py`，报告 `output/workspace-label-live-result.json`，写入 0。

运行状态：用户授权取消烟火验证后，后台已更新，健康检查通过。运行中的真实 API 对 fire=1395 张、smoke=646 张、star=0 张的列表、选择和邻居验证全部通过。前端检查 `X-Annotation-Label-Filter` 响应头，防止旧后台忽略 label 时把全部图片错误展示为筛选结果。筛选和 R 键均已生效。

最终浏览器回归使用真实 MainLayout 和工作台组件，接口与图片为夹具；1366×768 无页面溢出，筛选控件可见。截图 `output/playwright/workspace-label-review.png`。真实标注或人工审核写入 0。
