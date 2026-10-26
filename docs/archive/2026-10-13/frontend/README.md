# 前端开发文档

## 项目概述

这是一个基于 React + TypeScript + Ant Design 的图像标注数据管理平台前端应用。

## 技术栈

- **框架**: React 19.2.8
- **语言**: TypeScript 6.0.2
- **UI库**: Ant Design 6.6.6
- **路由**: React Router 7.18.4
- **HTTP客户端**: Axios 1.20.0
- **构建工具**: Vite 8.3.0
- **代码检查**: OxLint 1.81.0

## 项目结构

```
frontend/
├── src/
│   ├── api/                 # API接口层
│   │   ├── client.ts        # Axios客户端配置
│   │   ├── auth.ts          # 认证API
│   │   ├── projects.ts      # 项目管理API
│   │   ├── assets.ts        # 资产管理API
│   │   ├── annotations.ts   # 标注管理API
│   │   └── smarteye.ts      # SmartEye集成API
│   ├── components/          # 可复用组件
│   ├── pages/              # 页面组件
│   │   ├── LoginPage.tsx    # 登录/注册页
│   │   ├── DashboardPage.tsx # 仪表盘
│   │   ├── ProjectsPage.tsx  # 项目管理
│   │   ├── AssetsPage.tsx    # 资产管理
│   │   ├── AnnotationsPage.tsx # 标注管理
│   │   └── SmartEyePage.tsx  # SmartEye数据拉取
│   ├── layouts/            # 布局组件
│   │   └── MainLayout.tsx   # 主应用布局
│   ├── hooks/              # 自定义Hooks
│   ├── utils/              # 工具函数
│   ├── types/              # TypeScript类型定义
│   ├── App.tsx             # 应用根组件
│   ├── main.tsx            # 应用入口
│   └── index.css           # 全局样式
├── public/                 # 静态资源
├── index.html             # HTML模板
├── vite.config.ts         # Vite配置
├── tsconfig.json          # TypeScript配置
└── package.json           # 项目依赖

```

## 核心功能模块

### 1. 认证系统
- **登录/注册**: 支持用户名密码登录和新用户注册
- **Token管理**: JWT令牌自动存储和请求头注入
- **路由守卫**: 未登录自动重定向到登录页
- **401处理**: Token过期自动跳转登录

### 2. 项目管理
- 项目列表展示（分页）
- 创建新项目（检测/分类/分割）
- 编辑项目信息
- 删除项目（带确认）
- 项目状态管理

### 3. 资产管理
- 按项目筛选资产列表
- 单个/批量文件上传
- 图片预览和查看
- 资产删除
- 文件去重（基于SHA-256）
- 拖拽上传支持

### 4. 标注管理
- 标注列表展示
- 按审核状态筛选
- 标注详情查看
- 标注审核（通过/驳回）
- 版本管理

### 5. SmartEye集成
- 查看支持的平台列表
- 创建数据下载任务
- 任务进度监控
- 零拷贝导入到项目

### 6. 仪表盘
- 关键指标统计
- 标注完成度可视化
- 最近项目列表
- 快速操作入口

## API接口配置

### 基础配置
- **后端地址**: `http://localhost:8000`
- **API前缀**: `/api`
- **超时时间**: 30秒

### 请求拦截器
```typescript
// 自动添加Authorization头
config.headers.Authorization = `Bearer ${token}`;
```

### 响应拦截器
```typescript
// 401 - 自动跳转登录
// 其他错误 - 显示错误消息
```

## 开发指南

### 启动开发服务器

```bash
cd frontend
npm install
npm run dev
```

前端将运行在 `http://localhost:3000`

### 构建生产版本

```bash
npm run build
```

构建产物在 `dist/` 目录

### 代码检查

```bash
npm run lint
```

## 环境要求

- Node.js >= 18
- npm >= 9

## API代理配置

开发环境下，Vite会自动代理API请求：

```typescript
proxy: {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
  },
  '/uploads': {
    target: 'http://localhost:8000',
    changeOrigin: true,
  },
}
```

## 主题定制

主题色采用紫色渐变：

```typescript
colorPrimary: '#667eea'  // 主色调
colorSuccess: '#10b981'  // 成功色
colorWarning: '#f59e0b'  // 警告色
colorError: '#ef4444'    // 错误色
colorInfo: '#38bdf8'     // 信息色
```

## 路由配置

```
/login                    # 登录页
/                        # 仪表盘（需要登录）
/projects                # 项目管理
/assets                  # 资产管理
/annotations             # 标注管理
/smarteye                # SmartEye集成
```

## 测试账号

**管理员**:
- 用户名: `admin`
- 密码: `<由环境变量设置密码>`

**普通用户**:
- 用户名: `user1`
- 密码: `<由环境变量设置密码>`

## 常见问题

### 1. 跨域问题
前端通过Vite代理解决，生产环境需要后端配置CORS。

### 2. Token失效
自动清除本地存储并跳转登录页。

### 3. 图片无法显示
检查后端静态文件服务是否正常运行。

## 后续优化

1. **性能优化**
   - 代码分割（React.lazy）
   - 图片懒加载
   - 虚拟滚动（大列表）

2. **功能增强**
   - 标注编辑器集成
   - 批量操作优化
   - 数据导出功能
   - 实时协作

3. **用户体验**
   - 离线缓存
   - 加载骨架屏
   - 操作撤销/重做
   - 快捷键支持

## 联系方式

遇到问题请查看后端API文档：`http://localhost:8000/docs`
