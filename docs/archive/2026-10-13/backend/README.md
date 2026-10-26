# 数据管理平台

图片标注和训练管理平台，支持从SmartEye拉取数据、零拷贝导入、智能标注和模型训练。

## 项目特性

### 🎯 核心功能

- **SmartEye集成**: 从多个子平台拉取图片数据，统一管理
- **零拷贝导入**: 引用已下载的图片，无需重复下载，节省存储和时间
- **统一存储抽象层**: 支持本地存储和EOS对象存储，无缝切换
- **智能标注**: 集成Qwen视觉模型，自动预标注
- **多轮审核**: 支持自动审核和人工复核
- **模型训练**: 一键提交训练任务到K8s集群

### 🚀 技术亮点

- **零拷贝架构**: SmartEye下载的图片可以瞬间导入标注项目（1000张 < 5秒）
- **统一存储层**: 切换存储后端只需修改配置，代码无需改动
- **异步架构**: 基于FastAPI的异步处理，高并发性能
- **类型安全**: 全栈TypeScript，减少运行时错误
- **暗色设计**: 专业的深色主题UI，减少眼疲劳

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- PostgreSQL 15+
- (可选) EOS对象存储

### 1. 克隆项目

```bash
git clone <repository-url>
cd 数据管理平台
```

### 2. 启动后端

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/Scripts/activate  # Windows
# source venv/bin/activate    # Linux/Mac

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，配置数据库连接

# 启动服务
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

后端运行在：http://localhost:8000
API文档：http://localhost:8000/docs

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端运行在：http://localhost:5173

## 项目结构

```
数据管理平台/
├── backend/                    # FastAPI后端
│   ├── api/                    # API路由
│   ├── models/                 # 数据库模型
│   ├── services/               # 业务逻辑
│   │   ├── storage/            # 统一存储层 ⭐
│   │   └── smarteye/           # SmartEye服务
│   ├── config/                 # 配置
│   ├── tests/                  # 测试
│   └── main.py                 # 入口
│
├── frontend/                   # Vue3前端
│   ├── src/
│   │   ├── api/                # API接口
│   │   ├── views/              # 页面组件
│   │   ├── router/             # 路由
│   │   └── utils/              # 工具函数
│   └── package.json
│
├── docs/                       # 文档
│   ├── 需求梳理_v0.3.md
│   ├── 实现方案_v1.0.md
│   ├── 架构设计-SmartEye集成方案.md
│   ├── API接口规范_v1.0.yaml
│   ├── 数据库详细设计_v1.0.md
│   └── 测试计划_v1.0.md
│
└── README.md
```

## 核心特性详解

### 统一存储抽象层

```python
from services.storage import get_storage

# 自动根据配置返回存储实例
storage = get_storage()

# 保存文件
result = await storage.save_file(file_data, "path/to/file.jpg")

# 零拷贝（本地：硬链接，EOS：服务端复制）
await storage.copy_file(src_path, dst_path)
```

**切换存储后端**：

```bash
# 本地存储
STORAGE_TYPE=local

# EOS存储
STORAGE_TYPE=eos
EOS_ENDPOINT=http://eos.example.com
EOS_BUCKET=data-platform
```

### 零拷贝导入

```
SmartEye任务 (smarteye_downloads表)
  └─ blob_path: "smarteye/task_001/abc.jpg"
      ↓ 零拷贝引用
标注项目 (assets表)
  └─ blob_path: "smarteye/task_001/abc.jpg"  # 同一路径！
  └─ source_record_id: download.id           # 关联源记录
```

**优势**：
- ✅ 瞬间完成（1000张 < 5秒）
- ✅ 节省存储空间（不重复存储）
- ✅ 支持本地和EOS

### API端点概览

#### SmartEye
- `GET /api/smarteye/platforms` - 平台列表
- `POST /api/smarteye/tasks` - 创建下载任务
- `GET /api/smarteye/tasks` - 任务列表
- `POST /api/smarteye/import-to-project` - 零拷贝导入 ⭐

#### 项目管理
- `POST /api/projects` - 创建项目
- `GET /api/projects` - 项目列表
- `GET /api/projects/{id}/assets` - 项目图片
- `POST /api/projects/{id}/upload` - 上传图片

完整API文档：http://localhost:8000/docs

## 开发指南

### 运行测试

```bash
cd backend
pytest tests/ -v --cov=services
```

### 数据库迁移

```bash
cd backend
alembic init migrations
alembic revision --autogenerate -m "初始化数据库"
alembic upgrade head
```

### 构建Docker镜像

```bash
# 后端
docker build -t data-platform-backend ./backend

# 前端
cd frontend
npm run build
docker build -t data-platform-frontend .
```

## 配置说明

### 后端环境变量

| 变量 | 说明 | 默认值 |
|-----|------|--------|
| `DB_HOST` | 数据库地址 | 192.168.1.115 |
| `DB_PORT` | 数据库端口 | 5432 |
| `DB_USER` | 数据库用户 | postgres |
| `DB_PASSWORD` | 数据库密码 | - |
| `DB_NAME` | 数据库名称 | data_platform |
| `STORAGE_TYPE` | 存储类型 | local |
| `STORAGE_LOCAL_PATH` | 本地存储路径 | ./data |

### 前端环境变量

```bash
# .env.development
VITE_API_BASE_URL=/api

# .env.production  
VITE_API_BASE_URL=https://api.example.com
```

## 部署

### Docker Compose（推荐）

```bash
docker-compose up -d
```

### K8s部署

```bash
kubectl apply -f k8s/
```

## 文档

- 需求文档（历史引用缺失：`./docs/需求梳理_v0.3.md`） - 完整的功能需求
- 技术方案（历史引用缺失：`./docs/实现方案_v1.0.md`） - 详细的技术实现
- [架构设计](../../../design/架构设计-SmartEye集成方案.md) - SmartEye集成方案
- [API规范](../../../api/API接口规范_v1.0.yaml) - OpenAPI 3.0格式
- [数据库设计](../../../design/数据库详细设计_v1.0.md) - ER图和表结构
- [测试计划](../../../testing/测试计划_v1.0.md) - 完整的测试用例

## 技术栈

### 后端
- **框架**: FastAPI 0.104.1
- **数据库**: PostgreSQL 15 + SQLAlchemy 2.0 (async)
- **存储**: boto3 (S3兼容)
- **测试**: pytest + pytest-asyncio

### 前端
- **框架**: Vue 3.4
- **语言**: TypeScript 5.3
- **UI库**: Element Plus 2.5
- **构建**: Vite 5.0
- **状态**: Pinia 2.1

## 开发路线图

### Phase 1: 基础功能 (已完成) ✅
- [x] 项目脚手架
- [x] 统一存储抽象层
- [x] SmartEye数据拉取
- [x] 项目管理
- [x] 零拷贝导入

### Phase 2: 标注功能 (开发中) 🚧
- [ ] 标注工作台（Konva.js）
- [ ] 标注历史版本
- [ ] 快捷键支持
- [ ] 批量操作

### Phase 3: 智能审核 (计划中) 📋
- [ ] Qwen视觉模型集成
- [ ] 自动预标注
- [ ] 多轮审核流程
- [ ] 质量评分

### Phase 4: 模型训练 (计划中) 📋
- [ ] 训练任务管理
- [ ] K8s集成
- [ ] 模型评估
- [ ] 模型下发

## 贡献指南

我们欢迎所有形式的贡献！

1. Fork本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开Pull Request

## 问题反馈

遇到问题？请在 Issues（历史引用缺失：`../../issues`） 提交问题报告。

## 许可证

MIT License

## 致谢

- 感谢 [FastAPI](https://fastapi.tiangolo.com/) 提供的优秀Web框架
- 感谢 [Vue.js](https://vuejs.org/) 和 [Element Plus](https://element-plus.org/)
- 感谢所有开源贡献者

---

**开发团队** | 数据管理平台项目组 | 2024
