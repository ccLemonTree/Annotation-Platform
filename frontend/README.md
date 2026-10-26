# 主前端

React + TypeScript + Ant Design应用，入口是[src/App.tsx](src/App.tsx)，页面在`src/pages/`，API调用在`src/api/`及`src/services/`。

图片管理上传使用`ImageUploadDialog`及`api/imageUploads.ts`：图片、文件夹、ZIP/TAR统一8MiB分片，原生Web Crypto逐片校验，暂停/断开/刷新后重选同文件续传。需要localhost或HTTPS；子目录图片加入同一目标图片集，入库规则/场景沿用后端。浏览器脚本`scripts/verify_resumable_upload_ui.cjs`拦截上传写入，不创建业务样本；操作与限制见[上传说明](../docs/development/文件夹与压缩包上传.md)。

关闭上传弹窗只隐藏窗口，已启动队列继续运行，重开保留目标规则、进度和完成统计；明确点击暂停或组件卸载才中止浏览器传输。刷新/离开图片管理页仍需重选续传，服务端已排队入库继续。`scripts/verify_upload_dialog_lifecycle.cjs`用受控分片/入库响应验证这些时序，全部上传写入拦截。

工具栏/空列表上传入口共用`openUpload`，默认当前筛选→本浏览器上次使用且仍存在的图片集→已有第一集，无现有集留空，不自动用当天日期。显式输入新名称仍支持；开始时保存选择，刷新后以当前服务端图片集列表校验。回归见`scripts/verify_upload_collection_default.cjs`。

## 开发

在PowerShell 7中执行：

```powershell
Set-Location 'F:\hzxf\数据管理平台\frontend'
npm run dev
```

缺少依赖时先执行`npm ci`。Vite配置端口为3000，后端端口为8000；主API客户端默认使用同源请求，可由`VITE_API_BASE_URL`覆盖；开发时通过Vite代理到8000。SmartEye请求使用同源`/api/smarteye`，开发时由Vite转发到8000，部署时需配置相同反向代理。

```powershell
npm run build
npm run lint
```

`smarteye/`保留原SmartEye六组React页面，使用iframe隔离原样式，与主应用一起构建到`dist/`，无需第二个前端服务。主侧栏“数据拉取”入口地址为`/smarteye`，选择用于标注后先选项目。登录Token用于原图和ZIP的认证请求。

标注交互与快捷键见[检测标注操作说明](../docs/development/检测标注操作说明.md)。项目管理中新建/编辑可配置标注类别；类别改名时设置旧→新映射，同步最新框并生成待审核版本；工作台新增滚轮缩放、平移、重叠选择、删除及本地撤销重做。开发测试使用 `node --experimental-strip-types --test tests/detection-editor.test.mjs`，不需额外测试依赖。

图片管理默认独立图片库，上传无需项目；可命名图片集为1013/1014或已有名称追加，按名字筛选并多选加入；SmartEye按平台名归类。名称／来源／首次入库批次／时间可筛选，多选后添加到项目，可多次使用不同批次。同图可用于多个项目。图片库固定为`/assets`；项目管理“管理图片”进入`/projects/<项目UUID>/images`，显示项目名称和独立标注／审核状态。“从图片库添加”在当前项目打开抽屉，成功、失败及取消均不切换页面；失败保留选择。新建项目成功直接进入其图片页。旧`/assets?project_id=`链接继续兼容。见[图片库说明](../docs/development/图片库与项目使用说明.md)。

图片库可先新建空图片集，选中后编辑“图片集设置”；SmartEye选择目标图片集后共用相同对话框。每集独立保存分辨率、模型地址/名称、分类开关和场景名单，管理员可修改。上传/拉取默认按目标图片集阈值，也可覆盖本次；初始720，任一边大于等于阈值即可，0关闭；图片库显示场景与处理状态，支持场景筛选和多选分类/重试。同集内容去重，场景跟原图走，同图多集共享成功分类；手动重分类按所选集规则，更新所有所属集。模型后台处理，失败不丢图片。操作见[场景与入库规则](../docs/development/图片场景与入库规则.md)。

标注与审核先在`/annotations`展示项目卡片；进入`/annotations?project_id=<UUID>`显示此项目最新标注的预览、名称、框数、版本和状态。状态/分页保存在URL，工作台通过`from=review`保留返回上下文，切图不丢失。审核统一在画框工作台核对图片后进行，旧JSON弹窗/列表快捷审核已移除。

P2检测工作台位于`/annotate/<资产UUID>`，项目图片/标注列表提供入口，工作台返回链接指向原项目；支持画框/移动/尺寸和类别编辑/删除/负样本/历史/审核。路由使用Data Router，切图前自动保存，失败停留原图并保留草稿；离开工作台仍提示未保存修改，历史版本只读。项目图片页提供导出类别/审核范围、YOLO/COCO包、划分/冻结与重复下载。

2026-10-13 P2主入口与SmartEye入口构建通过，浏览器实际test-img画两个“人”框、重开/审核/历史/离开确认和图片包下载已验证。真实外部/付费生成未触发，自动模型标注排P3；lint仍有警告，主包有大小提示。详见[当前TODO](../docs/development/TODO.md)。

桌面检测工作台（宽度≥992px）按窗口剩余高度布局，画布完整适应，右侧属性/框列表独立滚动；窄屏保留纵向布局。窗口回归脚本为`scripts/verify_workspace_viewport.cjs`，坐标/缩放与自动保存回归保留。

界面统一规范见[DESIGN.md](DESIGN.md)。两个入口共用设计变量及Ant Design主题；SmartEye保留六个入口并与主平台同样使用浅色工作台、蓝色主操作、统一控件和弹窗。验收脚本及范围见设计规范。

[项目入口](../README.md) · [文档目录](../docs/README.md) · [原前端说明与报告](../docs/archive/2026-10-13/frontend/README.md)
