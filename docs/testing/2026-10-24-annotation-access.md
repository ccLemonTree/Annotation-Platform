# 非管理员标注页面访问

## 行为与范围

- `admin` 保留原有菜单、项目工作流及页面。
- 其他角色登录后进入 `/annotations`，仅允许标注项目、项目审核、单图标注和向量标注页面。
- 非标注地址统一替换为 `/annotations`，受限子页面不挂载；桌面/手机菜单、功能搜索、相关功能和项目导航同步隐藏管理入口。
- 首次进入应用通过 `/api/auth/me` 核对角色，覆盖旧身份缓存并共享给页面。账户读取失败时阻断应用，未登录跳转登录页。
- 标注页面内的管理跳转和无图片提示适配普通用户；保留人工标注、审核、继续标注与图片切换操作。
- 本轮仅限制前端页面访问；后端 API 认证与授权沿用原规则，无数据库迁移、业务数据或远端写入。

## 验证

1. `npm run build`：通过，保留原有大包提示。
2. 改动文件定向 `oxlint`：退出 0，只有原标注页/检测工作台的 effect 和依赖警告，新增认证文件无警告。
3. `scripts/verify_annotation_access.cjs`：18 项检查，覆盖服务端角色优先于伪造管理员缓存、19 个受限/未知地址、功能搜索、标注内部链接、四类标注页面、人工描述保存、手机导航、管理员、登录及账户请求失败。API 全部隔离，零业务写入。
4. `scripts/verify_navigation_context.cjs`：审核来源、页码和返回链接通过。
5. `scripts/verify_navigation_workspaces.cjs`：管理员检测画布、旧项目图片地址、项目错误返回和 SSH 到 GPU 服务器上下文通过。
6. `scripts/verify_platform_navigation.cjs`：管理员项目准备/模型配置/队列/标注/审核/导出/训练/验证、未保存保护、7 个手机页面及普通用户限制通过。

专项测试曾在暂时移除菜单过滤时按预期失败，恢复过滤后通过。旧导航夹具补齐账户身份、GPU 任务和智搜平台响应，并同步普通用户跳转预期。

证据：`output/annotation-access-red.txt`、`output/annotation-access-verification.txt`、`output/annotation-access-workspaces.txt`、`output/annotation-access-platform.txt`；截图 `output/playwright/annotation-access-user.png`。

未使用真实普通账号写入业务标注，未重跑完整后端测试。
