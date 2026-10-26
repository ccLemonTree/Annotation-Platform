# 交集判定与方向键切图

## 行为

- 模型效果预览、验证结果“真值与预测”弹窗支持左右方向键及上一张/下一张按钮。跨页继续切图，首尾不循环；弹窗沿当前错误类型、标签、判定方式的结果顺序导航。
- 输入框、选择框、输入法组合输入及带修饰键的方向键保留编辑作用；关闭弹窗后不继续切图。
- 目标检出模式使用 `交集面积 / min(真值框面积, 预测框面积) >= target_overlap`，默认 0.2。完全包含对应 1；同类别一对一匹配，同一已检出目标的重复预测合并。
- 验证报告可调整“交集阈值（%）”（1–100，整数），当前报告、逐图筛选、混淆矩阵和 JSON 导出使用相同参数。重新打开报告回到默认 20%。
- 标准框匹配和标准 AP 保留原任务 IoU 规则、置信度工作点、每图每类最多 100 个预测。业务交集值在导出中标记为 `overlap`，方法为 `intersection_over_min_area`，不冒充 IoU。
- 修改仅重新计算已保存的预测，不启动模型推理，不修改真值、预测、原任务配置或已存标准结果。

## 验证

- 指标/目标视图/标签筛选：83 passed，包括恰好 20%、低于阈值、双向包含、错误类别、一预测不能匹配两个真值、极大极小坐标、标准 AP 对照。
- 验证任务、删除、图片预览、checkpoint 结果回传及校验：97 passed。
- 前端 `npm run build` 成功；原有产物体积提示仍存在。
- 浏览器受控响应检查：预览 21 图跨页、前后边界、输入焦点、刷新选择保持；弹窗 21 图跨页、标签和漏检筛选参数保持、关闭释放快捷键；报告阈值、筛选、下载一致及 390px 布局。
- 实际 LAN 页面：现有 11 图 checkpoint 预览，及现有验证报告 fire 漏检弹窗，左右切换正常；只允许 GET，未触发推理或业务写入。
- 重启后端前确认上传、验证、训练、自动标注、语义与项目处理无活动任务，checkpoint 执行保留为空。
- 3945 张真实结果只读重算：TP 1962、FP 111、FN 119、合并重复预测 73。12 组标签/错误/模式筛选的总数及首 100 条顺序均与计算结果一致；原配置、指标、真值、预测和逐图原始评测的数据库摘要前后相同。
- 此前四张示例中：第 2 张 TP1/FP0/FN0；第 4 张 TP1/FP0/FN0、合并重复框 1；第 1 张右侧仍漏 2 个真值框，第 3 张仍漏 fire 1 个。

证据：`output/validation-overlap-live-result.json`、`output/validation-overlap-regression.log`、`output/validation-overlap-frontend-build.log`、`output/playwright/preview-navigation.log`、`output/playwright/validation-navigation.log`、`output/playwright/validation-target-overlap.log`、`output/playwright/navigation-live.log` 及同目录截图。
