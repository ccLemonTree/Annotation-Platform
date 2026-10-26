# P0基础修复实施计划

> For Codex: 按当前TODO逐项实施；先运行失败测试，再做最小修复，按阶段记录实际结果。

**Goal:** 让现有项目能够构建、正确使用API，并具备安全认证、图片读取和可重复测试的基础。

**Architecture:** 保留当前FastAPI与React主入口；前后端采用现有后端的id/UUID契约。测试采用隔离数据库或依赖覆盖；旧SmartEye模拟实现不通过恢复过时表来冒充正式接入。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、pytest、React、TypeScript、Vite、Ant Design。

## 1. 可恢复基线与回归测试

- 本轮源文件基线：`C:/Users/chen0/AppData/Local/Temp/data-platform-p0-20261013-114913/source-baseline.zip`，210个文件；未包含.env、业务数据和依赖目录。
- 初始化本地版本管理，忽略配置秘密、数据、缓存及依赖。
- 修改`backend/backend/tests/conftest.py`：移除硬编码远程测试库及drop_all流程。
- 在`backend/backend/tests/`增加真正的API契约、权限、存储回归测试；先确认失败来自已发现的缺陷。

## 2. 前端构建与接口契约

- 修改`frontend/src/api/{projects,assets,annotations,auth,client}.ts`及相关页面：主键id、数字tenant_id、项目status、统计字段、上传返回和批量请求。
- 修改`backend/backend/schemas/project.py`及项目路由：标签结构保存/回读、任务类型筛选。
- 修改`backend/backend/api/smarteye_routes.py`：项目ID使用UUID；正式接入仍按P1实施。
- 修复Dashboard/DetectionQuery编译错误；Dashboard使用统计API，避免只累计前5个项目。
- 验证：`npm run build`；隔离API测试确认合法UUID和页面请求契约。

## 3. 认证与错误处理

- 修改`api/deps.py`、用户、标注、批量及SmartEye路由：role管理员判断、统一身份与资源范围检查。
- 修改`config/settings.py`、`services/auth/jwt.py`、`.env.example`和入口：密钥来自配置；本地配置生成密钥，不纳入版本管理。
- 验证：未登录401、普通用户越权403/404、管理员合法操作、审核筛选正确、错误响应可序列化。

## 4. 安全存储与图片使用

- 修改`services/storage/{base,local,eos}.py`及资产路由：统一保存/读取契约，路径限制、图像验证、大小与尺寸、内容哈希路径。
- 添加认证图片内容端点，前端通过带Token的请求生成预览URL。
- 资源删除先移除引用；在完整下载引用体系接入前保留物理文件，防止误删共享图片。
- 验证：越界路径拒绝，真实图片上传/读取，重复上传不产生第二份文件，删除一个引用后另一个仍可读。

## 5. 数据库与环境

- 核对当前库postgres、版本004及现有数据；迁移前取得并验证备份。
- 在隔离目标验证非破坏迁移，再应用到明确目标；不执行003/004的删表流程，不恢复模拟服务用的过时表。
- 对齐requirements与Compose中的数据库、存储和JWT设置；Docker未实际运行前保留“未验收”。

## 6. 最终检查与状态更新

- 运行针对本轮逻辑的pytest、前端build以及非空图片/标注流程；再核对实际启动服务。
- 更新`docs/development/TODO.md`，仅勾选满足对应验收条件的事项。
- SmartEye完整源码迁移、可视化画框、正确COCO/YOLO交付以及模型/训练仍按P1—P4执行，不因基础修复声明完成。
