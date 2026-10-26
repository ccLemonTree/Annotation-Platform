# Model Trial Image Picker Implementation Plan

**Goal:** 试运行图片选择支持分页、图片集、入库时间及文件名组合筛选，保留手输 ID。

**Architecture:** 使用现有 getImages/getImageCollections 接口及 Ant Design 控件；新增专用单图选择组件，嵌入现有试运行表单。不改后端、主页及模型服务。

1. 增加浏览器回归用例，验证历史页选择、组合筛选、页码重置、清空旧 ID、迟到响应隔离和只读行为；先确认旧实现失败。
2. 在 `frontend/src/components/TrialImagePicker.tsx` 实现筛选与分页，沿用图片管理的 UTC 时间参数转换。
3. 在 `ModelServicesPage.tsx` 接入组件，清除旧下拉框请求逻辑；关闭弹窗和切换图片来源时卸载选择器。
4. 运行专项和既有模型管理浏览器回归、前端构建；检查真实 API 组合查询，并核对桌面和窄屏截图。
