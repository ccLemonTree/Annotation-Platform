# 图像数据平台界面规范

适用：登录、概览、项目、图片、标注/审核、检测工作台，以及 SmartEye 六个主功能页、台账/日志详情和图片预览。2026-10-13 使用 ui-skills MCP 的 [baseline-ui](https://www.ui-skills.com/skills/ibelick/baseline-ui/llms.txt) 与 [fixing-accessibility](https://www.ui-skills.com/skills/ibelick/fixing-accessibility/llms.txt) 实施。

## 共享入口

- 2026-10-16 用户选定 A 钴蓝工程台。设计变量：[tokens.css](src/ui/tokens.css)：强调色 #699cff、主按钮 #2c62d7、背景 #0b101b、表面 #121b2a、浮层 #192438、边框 #28354a、正文 #e8eef9、辅助文字 #a1afc4。避免持续闪烁、大范围发光和扫光。
- 两个入口均复用 [PlatformProvider](src/ui/PlatformProvider.tsx)，Ant Design 主题从设计变量读取。SmartEye Tailwind 的 primary/background/font 同样引用这些变量。
- 保留 SmartEye iframe，两个应用一起构建、同源鉴权，共用钴蓝暗色主题及 Ant Design darkAlgorithm。SmartEye 启用已有 Tailwind dark 样式，原生表单使用 dark color-scheme；不增加远程字体或模拟管理员头像。
- 字体使用系统字体及 Microsoft YaHei。圆角 8px，基础控件高 36px，正常文本 14px，数值使用等宽数字。主平台标题 26px，内嵌模块标题 22px，卡片标题 16px。

## 页面和组件

- 主导航保留工作概览，并分为数据资产、标注与质量、模型与算力三组；原有路由和管理员入口权限不变。桌面可收为图标轨道，宽度小于 992px 使用导航抽屉，选择入口后关闭。旧 /detection-query 路由仍指向 SmartEye。
- SmartEye 六个入口改为横向 Menu，窄屏多余入口通过“更多功能”点击展开。用途选择和任务抽屉复用 Ant Design。
- 卡片保持轻边框，不使用发光；概览当前工作卡允许轻微钴蓝背景渐变。蓝色用于主操作/选中，红色、绿色、琥珀色仅表达错误/成功/警告；次要操作用深色表面或文字按钮。
- 工作概览按用户确认的 A 方案使用四项指标和两行双列卡片：当前工作/图片巡览、处理队列/算力概况，小屏单列。图片资产是独立原图数；审核和待标注按项目图片引用统计，待审核/返工包含驳回。当前工作读取最近检测项目的审核进度，待审入口携带项目和 pending 筛选。
- 图片巡览读取图片库最近四图，复用鉴权原图组件与 PagedAssetPreview，支持放大、左右键和 Escape。处理队列汇总最近预标注/审核、语义、验证任务，管理员另读训练任务，优先展示进行中的任务，最多三条。算力卡仅管理员读取已启用服务器快照，可选服务器/显卡，显示采集时间，不轮询；未知指标显示 --。任务与算力单独刷新，部分请求失败保留成功来源，无数据使用空态。
- 表格列宽不足时在表格内滚动，页面不水平溢出。工具栏允许换行；窄屏标注侧面板移到画布下。
- 新建下载/监听、设备更新、检测详情使用 Modal；AI 历史和任务记录使用 Drawer。图片预览复用 [ImagePreview](smarteye/components/ImagePreview.tsx)。台账输入选择复用 AutoComplete，允许手动填写原先支持的任意平台名称。
- 交互动效：160ms 按钮颜色/按下反馈及桌面导航展开过渡，240ms 页面内容淡入并上移 6px；弹窗和抽屉沿用 Ant Design 自带动画。只对非标注页面的内容应用进场动画；标注画布及其祖先不做 transform/进场动画，标注页导航宽度立即切换。PlatformProvider 初次加载时依据系统 prefers-reduced-motion 设置 Ant Design token.motion；CSS 媒体查询在系统设置变化时即时取消自定义进场/位移，并将其余过渡压至 .01ms，保留需要结束事件的兼容行为，避免切换组件库 MotionProvider 导致工作区重挂载。
- 输入应有标签，纯图标操作应有可访问名称，可见键盘焦点必须保留。复杂弹窗的焦点和 Escape 行为交给 Ant Design。未对全平台作完整 WCAG 审计。

## 验收与边界

浏览器脚本在项目根目录使用 Playwright CLI 的 run-code --filename 执行；需要已启动的主前后端、有效登录状态和 test-img 示例项目。私有登录状态放 .backups，截图放 output/playwright，均不入库。

- [布局和主题检查](../scripts/verify_unified_ui.cjs)：主平台五个业务页和 SmartEye 六页的 1440px / 390px 显示、用途和任务抽屉。
- [弹窗和导航检查](../scripts/verify_unified_ui_interactions.cjs)：七个弹窗/抽屉、键盘切换、Escape、窄屏菜单、登录/注册标签。
- [任务按钮检查](../scripts/verify_unified_ui_task_controls.cjs)：浏览器拦截数据，检查取消/重试/错误和认证，不操作真实任务。
- [预览检查](../scripts/verify_unified_ui_previews.cjs)：浏览器拦截查询，验证检测详情和共享图片预览，不查询真实下级平台。
- [钴蓝主题与动效检查](../scripts/verify_cobalt_ui.cjs)：隔离 API 样本，检查主平台及 SmartEye、三种窗口宽度、表单/弹窗、导航抽屉、标注画布不重挂载、权限入口及减少动态效果；不会发出真实业务或远程请求。
- [工作概览布局检查](../scripts/verify_dashboard_layout.cjs)：隔离 API 样本，验证四项指标/四块卡片、项目与全局统计、2320/1440/768/390px 布局、预览、显卡切换、失败重试、空态和普通用户权限；不写入业务数据。

2026-10-16 A方案落地验收：构建成功，lint退出0（44条既有警告），检测编辑器5项测试通过。49组隔离浏览器检查通过，并单独记录真实页面进场 animationstart/animationend 和次要按钮暗色显示；部分管理员页面检查的是现有错误状态样式。完整范围见[验收JSON](../docs/testing/cobalt-ui-verification-2026-10-16.json)。

2026-10-16 工作概览按参考图重排：10组专项浏览器检查及49组共享主题回归通过，构建成功，lint退出0（44条既有警告）；新增概览文件定向检查无警告。详细范围见[概览验收JSON](../docs/testing/dashboard-layout-verification-2026-10-16.json)。图片使用本地样本字节，API均拦截；真实服务器快照与业务端到端另行验证。

此次不修改后端、图片、已保存标注或冻结快照，不触发真实下载/收费生图。SmartEye 保留原 Tailwind 浏览器运行脚本；生产打包有体积提示，lint 尚有历史 Hook 等警告。真实设备和生产部署验收另行进行。
