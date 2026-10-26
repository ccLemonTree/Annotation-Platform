# 向量与大模型描述标注快捷键验证

## 范围

- `ImageAnnotationWorkspace`：大模型描述、向量图片描述、分类共用编辑页。方向键或 A/D 切图，Ctrl/Cmd+S 保存，R 审核通过，Shift+R 驳回。
- `VectorAnnotation`：文本检索与以图搜图结果。方向键选择当前卡片，1/2/3 保存正确、错误、无法判断。
- 共用快捷键监听排除输入、组合输入、长按重复、弹窗和选择器；描述页允许输入期间显式保存。原有未保存修改确认继续生效。
- 复用既有保存及审核接口，提交期间同步锁防止重复请求，已是目标审核/判定状态时不重复提交。

## 验证结果

2026-10-22，使用本机 Vite 与 Playwright：

- `scripts/verify_caption_shortcuts.cjs` 通过：切图并保留返回参数、首图边界、文本输入不误触、输入框内保存、通过/驳回及审核意见、重复通过抑制、未保存确认保留草稿、409 保存失败保留草稿并可重试、帮助弹窗抑制快捷键，以及向量描述/分类共用编辑页。5 次写请求全部由隔离 API 数据接收。
- `scripts/verify_vector_shortcuts.cjs` 通过：方向键选图、1/2/3 判定当前结果、版本正确、快速连续按键和同状态不重复写、文本输入和帮助弹窗不误触、以图搜图同样支持导航。3 次写请求全部由隔离 API 数据接收。
- `scripts/verify_annotation_shortcuts_live.cjs` 通过：现有向量查询真实结果导航、帮助、现有图片描述编辑页导航并保留返回上下文。真实业务写入与模型调用均为 0；检查了桌面和移动布局。
- `frontend` 的 `npm run build` 通过（TypeScript 与 Vite）；仅有现存的大包体积警告。

日志位于 `output/playwright/*shortcuts*.log` 与 `output/annotation-shortcuts-build.log`。真实平台当前没有独立描述/分类项目，该部分通过隔离浏览器数据验证；未修改现有标注，未调用模型，未重启后端。
