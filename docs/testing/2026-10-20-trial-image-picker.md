# 模型试运行图片选择器验收

- 入口：模型管理 → 试运行 → 图片库图片 ID。
- 支持按图片集、文件名、入库起止时间组合筛选，分页及每页 20/50/100 张。
- 日期按浏览器本地时间输入，转 ISO UTC 后提交现有 created_from/created_to 参数；不依据图片拍摄时间。
- 筛选变化回第一页；翻页、改变每页数量或筛选时清空旧图片 ID，并清除旧推理预览。改变每页数量也回第一页。
- 保留手工输入图片 ID、项目 asset ID；关闭弹窗或切换来源时卸载选择器，迟到列表请求不会覆盖新结果。
- 图片列表或图片集请求失败有明确错误及重试入口。新选择器不写入标注或修改图片。

实现：`frontend/src/components/TrialImagePicker.tsx`，调用既有 getImages/getImageCollections；`ModelServicesPage.tsx` 仅负责接收选择并更新试运行表单。

验证：

1. 前端 TypeScript/Vite 构建通过，原有包体积提示保留。
2. 专项浏览器用例 `scripts/verify_trial_image_picker.cjs` 通过：历史第 2 页选择及同 ID 预测、每页数量切换、组合查询及 UTC 边界、页码重置、不同内容的迟到响应隔离、桌面与 390px 窄屏、错误后重试。预测请求为 mock，不写业务数据。
3. 既有模型管理浏览器回归 `scripts/verify_model_services.cjs` 的 9 项检查通过，涵盖 CRUD、权限、预览、映射、撤销、空框保护及迟到推理结果隔离。
4. 真实 API 只读查询：图片集“1019弥漫烟雾测试”共 187 张，第 1/2 页各 20 张，ID 无交集；图片集、入库日期、文件名组合筛选可找回第 2 页选定图片。
5. 相关文件 lint 无错误；ModelServicesPage 原有两条 effect 警告保留，新组件无 lint 警告。

浏览器记录和截图：

- `output/playwright/trial-image-picker-verification.log`
- `output/playwright/model-services-regression-20261020.log`
- `output/playwright/trial-image-picker-desktop.png`
- `output/playwright/trial-image-picker-mobile.png`

未进行大规模图片库压力测试。本次未修改后端、主页取图逻辑或远端模型服务。
