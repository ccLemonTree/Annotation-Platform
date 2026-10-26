# 创建时保存交集阈值与预览滚动保持

## 改动

- “验证训练权重”增加“交集阈值（%）”，默认 20，范围 1–100。百分比转换为 `config.target_overlap`，由现有任务 JSON 配置保存，无数据库迁移。
- “匹配 IoU”明确标为“标准评测匹配 IoU”，保留标准评测语义，与业务交集阈值分别设置。
- 目标检出报告、逐图筛选、JSON 导出在未指定临时阈值时读取任务设置。历史任务无该字段时使用 20%；查询覆盖值不修改任务设置，关闭重开报告恢复任务设置。
- 预览原 `1 / 1` 指分页，不是图片序号。分页明确显示“第几页 / 共几页（20 张/页）”，图片导航仍显示当前图片序号。
- 移除缩略图 `scrollIntoView` 对整页的纵向滚动，只调整缩略图容器的横向滚动。
- 图片加载/失败时按原尺寸比例占位；跨页读取期间保留上一个画面，禁用尚未加载完成的数据导航，避免内容高度骤降导致浏览器滚动位置被迫上移。

## 验证

- 修改前复现：同页方向键切图时纵向滚动值由 721 变为 248。
- 修改后受控浏览器逐帧采样：同页图片延迟加载、跨页读取期间纵向滚动位置保持，原方向键、首尾边界、输入焦点、刷新及按钮导航检查通过。
- 创建表单浏览器测试：默认 20，填写 35 时提交 `.35` 且标准 IoU 保持 `.5`；失败创建保留填写的 45；成功后报告读取 `.45`，临时改为 60 后关闭重开恢复 45。GPU/权重及 390px 布局回归通过；所有写入由测试响应拦截。
- 后端相关测试 67 passed；其他指标/标签筛选/删除/checkpoint 回归 117 passed，共 184。另补充并通过历史任务缺少字段时的回退断言。
- 前端构建通过，保留已有产物体积提示。
- 后端重启前确认上传、验证、训练、自动标注、语义、项目处理和场景分类均无活动任务，checkpoint 活动执行为空。没有取消用户任务。
- 实际 LAN 页面检查：11 图预览在方向键切换后纵向位移为 0；分页说明可见；实际验证任务创建弹窗可填写独立交集阈值并取消。未提交真实验证、训练或推理。

证据位于 `output/validation-saved-overlap-tests.log`、`output/validation-saved-overlap-legacy-tests.log`、`output/validation-config-scroll-regression.log`、`output/validation-config-scroll-build.log`、`output/playwright/preview-scroll-before.log`、`output/playwright/preview-scroll.log`、`output/playwright/checkpoint-overlap-form.log`、`output/playwright/config-scroll-live.log` 及对应截图。
