# 数据管理平台 - 前端

基于 Vue3 + TypeScript + Element Plus 的图片标注训练平台前端。

## 快速开始

### 1. 安装依赖

```bash
cd frontend
npm install
```

### 2. 启动开发服务器

```bash
npm run dev
```

访问：http://localhost:5173

### 3. 构建生产版本

```bash
npm run build
```

构建产物输出到 `dist/` 目录。

## 项目结构

```
frontend/
├── src/
│   ├── api/                # API接口
│   │   ├── smarteye.ts     # SmartEye接口
│   │   └── project.ts      # 项目接口
│   ├── assets/             # 静态资源
│   │   └── main.css        # 全局样式
│   ├── router/             # 路由配置
│   │   └── index.ts
│   ├── utils/              # 工具函数
│   │   └── request.ts      # axios封装
│   ├── views/              # 页面组件
│   │   ├── SmartEyeView.vue       # SmartEye数据拉取
│   │   ├── ProjectsView.vue       # 项目列表
│   │   ├── ProjectDetailView.vue  # 项目详情
│   │   └── TrainingView.vue       # 模型训练
│   ├── App.vue             # 根组件
│   └── main.ts             # 入口文件
├── index.html
├── vite.config.ts
├── tsconfig.json
└── package.json
```

## 核心功能

### 1. SmartEye 数据拉取
- 创建下载任务
- 查看任务列表和进度
- 零拷贝导入到标注项目

### 2. 标注项目管理
- 创建/查看项目
- 上传图片
- 从SmartEye导入（零拷贝）
- 图片浏览和筛选

### 3. 模型训练（开发中）
- 训练任务管理
- 模型评估
- 模型下发

## 设计系统

### 色彩方案（深色主题）

```css
/* 背景层级 */
--bg-deepest: #0A0D12    /* 最深背景 */
--bg-deep: #0F131C       /* 深背景 */
--bg-elevated: #161D2B   /* 浮起表面 */
--bg-hover: #1E2636      /* 悬停状态 */

/* 文字 */
--text-primary: #E2E8F0   /* 主文字 */
--text-secondary: #A0AEC0 /* 次要文字 */
--text-tertiary: #718096  /* 三级文字 */

/* 主题色 */
--primary: #38BDF8        /* 青蓝色 */
--success: #34D399        /* 绿色 */
--warning: #FBBF24        /* 黄色 */
--danger: #F87171         /* 红色 */
```

### 组件规范

- 圆角：`border-radius: 8px` (卡片) / `12px` (容器)
- 间距：`gap: 12px` / `16px` / `24px`
- 阴影：`box-shadow: 0 4px 12px rgba(56, 189, 248, 0.2)`

## API 集成

所有API请求通过Vite代理转发到后端：

```typescript
// vite.config.ts
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true
    }
  }
}
```

### 使用示例

```typescript
import { getProjects } from '@/api/project'

const loadProjects = async () => {
  const res = await getProjects({ limit: 20 })
  console.log(res.projects)
}
```

## 环境变量

```bash
# .env.development
VITE_API_BASE_URL=/api

# .env.production
VITE_API_BASE_URL=https://api.example.com
```

## 开发规范

### TypeScript类型定义

```typescript
// api/project.ts
export interface Project {
  id: string
  project_name: string
  task_type: string
  status: string
}
```

### 组件命名

- 页面组件：`XxxView.vue`
- 业务组件：`XxxComponent.vue`
- 通用组件：`BaseXxx.vue`

### API方法命名

- 获取列表：`getXxxs()`
- 获取详情：`getXxxDetail(id)`
- 创建：`createXxx(data)`
- 更新：`updateXxx(id, data)`
- 删除：`deleteXxx(id)`

## 浏览器兼容性

- Chrome >= 90
- Firefox >= 88
- Safari >= 14
- Edge >= 90

## 常见问题

### Q: 如何添加新页面？

```typescript
// 1. 创建页面组件
// src/views/NewView.vue

// 2. 添加路由
// src/router/index.ts
{
  path: '/new-page',
  name: 'new-page',
  component: () => import('@/views/NewView.vue')
}

// 3. 添加菜单项
// App.vue
<el-menu-item index="/new-page">
  <el-icon><Menu /></el-icon>
  <span>新页面</span>
</el-menu-item>
```

### Q: 如何修改主题色？

编辑 `src/assets/main.css` 中的CSS变量。

### Q: API请求失败如何调试？

1. 检查Vite代理配置
2. 查看浏览器Network面板
3. 确认后端服务运行在 `localhost:8000`

## 技术栈

- **框架**: Vue 3.4
- **语言**: TypeScript 5.3
- **UI库**: Element Plus 2.5
- **构建工具**: Vite 5.0
- **状态管理**: Pinia 2.1
- **路由**: Vue Router 4.2
- **HTTP客户端**: Axios 1.6

## 开发计划

- [ ] 标注工作台（Konva.js）
- [ ] 审核流程界面
- [ ] 训练任务管理
- [ ] 模型评估可视化
- [ ] 实时通知（WebSocket）
- [ ] 多语言支持（i18n）

## 贡献指南

1. Fork本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开Pull Request

## 许可证

MIT License
