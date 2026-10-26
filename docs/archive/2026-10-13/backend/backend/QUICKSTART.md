# 🚀 快速开始指南

## 📋 前置要求

- Python 3.11+
- PostgreSQL 14+
- Redis 6+ (可选，用于Celery)

## 🔧 安装步骤

### 1. 克隆项目

```bash
cd F:/hzxf/数据管理平台/backend/backend
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

创建 `.env` 文件：

```bash
# 数据库配置
DB_HOST=192.168.1.115
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=123456
DB_NAME=data_platform

# 存储配置
STORAGE_TYPE=local
STORAGE_LOCAL_PATH=./data
UPLOAD_DIR=./uploads

# 应用配置
APP_ENV=development
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173,http://localhost:8080
```

### 4. 初始化数据库

```bash
# 运行数据库迁移
alembic upgrade head

# 初始化测试数据
python init_data.py
```

### 5. 启动服务器

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

服务将运行在：
- **API服务**: http://localhost:8000
- **API文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/health

## 🧪 运行测试

### 完整测试套件

```bash
python test_all_apis.py
```

### CRUD操作测试

```bash
python test_crud_operations.py
```

### SmartEye集成测试

```bash
python test_smarteye_integration.py
```

## 📝 测试账户

### 管理员账户
- **用户名**: `admin`
- **密码**: `<由环境变量设置密码>`
- **角色**: admin
- **租户ID**: 2

### 普通用户
- **用户名**: `user1`
- **密码**: `<由环境变量设置密码>`
- **角色**: user
- **租户ID**: 2

## 🔑 API使用示例

### 1. 用户登录

```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -d "username=admin&password=<由环境变量设置密码>"
```

响应：
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

### 2. 创建项目

```bash
curl -X POST "http://localhost:8000/api/projects" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "我的第一个项目",
    "task_type": "detection",
    "description": "目标检测项目"
  }'
```

### 3. 获取项目列表

```bash
curl "http://localhost:8000/api/projects?page=1&page_size=20" \
  -H "Authorization: Bearer <token>"
```

### 4. 创建SmartEye下载任务

```bash
curl -X POST "http://localhost:8000/api/smarteye/tasks" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 2,
    "task_name": "小红书美食图片",
    "platform": "xiaohongshu",
    "keyword": "美食",
    "target_count": 50
  }'
```

### 5. 零拷贝导入到项目

```bash
curl -X POST "http://localhost:8000/api/smarteye/import-to-project" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": 1,
    "project_id": "<project-uuid>"
  }'
```

## 📊 API端点总览

### 认证 (Auth)
- `POST /api/auth/login` - 用户登录
- `POST /api/auth/register` - 用户注册
- `GET /api/auth/me` - 获取当前用户信息

### 项目管理 (Projects)
- `GET /api/projects` - 获取项目列表
- `POST /api/projects` - 创建项目
- `GET /api/projects/{id}` - 获取项目详情
- `PUT /api/projects/{id}` - 更新项目
- `DELETE /api/projects/{id}` - 删除项目
- `GET /api/projects/{id}/stats` - 获取项目统计

### 资产管理 (Assets)
- `GET /api/assets` - 获取资产列表
- `POST /api/assets/upload` - 上传单个文件
- `POST /api/assets/batch-upload` - 批量上传文件
- `GET /api/assets/{id}` - 获取资产详情
- `DELETE /api/assets/{id}` - 删除资产

### 标注管理 (Annotations)
- `GET /api/annotations` - 获取标注列表
- `POST /api/annotations` - 创建标注
- `GET /api/annotations/{id}` - 获取标注详情
- `PUT /api/annotations/{id}` - 更新标注
- `DELETE /api/annotations/{id}` - 删除标注
- `POST /api/annotations/{id}/review` - 审核标注

### SmartEye集成
- `GET /api/smarteye/platforms` - 获取支持的平台
- `POST /api/smarteye/tasks` - 创建下载任务
- `GET /api/smarteye/tasks` - 获取任务列表
- `GET /api/smarteye/tasks/{id}` - 获取任务详情
- `GET /api/smarteye/tasks/{id}/downloads` - 获取下载记录
- `POST /api/smarteye/import-to-project` - 零拷贝导入

## 🐛 常见问题

### 1. 数据库连接失败

**问题**: `Connection refused` 或 `Authentication failed`

**解决方案**:
- 检查PostgreSQL是否运行
- 验证 `.env` 中的数据库配置
- 确认数据库已创建: `CREATE DATABASE data_platform;`

### 2. 导入错误

**问题**: `ModuleNotFoundError: No module named 'xxx'`

**解决方案**:
```bash
pip install -r requirements.txt
```

### 3. 端口被占用

**问题**: `Address already in use`

**解决方案**:
```bash
# 查找占用端口的进程
netstat -ano | findstr :8000

# 终止进程
taskkill /PID <进程ID> /F

# 或使用其他端口
uvicorn main:app --port 8001
```

### 4. CORS错误

**问题**: 前端访问API时出现CORS错误

**解决方案**:
在 `.env` 中添加前端域名：
```bash
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

## 📁 项目结构

```
backend/
├── api/                    # API路由
│   ├── auth_routes.py     # 认证相关
│   ├── project_routes.py  # 项目管理
│   ├── asset_routes.py    # 资产管理
│   ├── annotation_routes.py # 标注管理
│   └── smarteye_routes.py # SmartEye集成
├── models/                 # 数据库模型
│   ├── user.py
│   ├── project.py
│   ├── asset.py
│   ├── annotation.py
│   └── smarteye.py
├── schemas/                # Pydantic schemas
├── services/               # 业务逻辑
│   ├── auth/              # 认证服务
│   ├── storage/           # 存储服务
│   └── smarteye_service.py # SmartEye服务
├── config/                 # 配置文件
│   └── settings.py
├── alembic/               # 数据库迁移
├── tests/                 # 测试文件
│   ├── test_all_apis.py
│   ├── test_crud_operations.py
│   └── test_smarteye_integration.py
├── main.py                # 应用入口
├── database.py            # 数据库连接
└── requirements.txt       # 依赖列表
```

## 🔗 相关文档

- [SUCCESS_REPORT.md](SUCCESS_REPORT.md) - 初次修复完成报告
- [CHANGELOG.md](CHANGELOG.md) - 更新日志
- [DEVELOPMENT_COMPLETE.md](DEVELOPMENT_COMPLETE.md) - 开发完成报告
- [API文档](http://localhost:8000/docs) - Swagger交互式文档

## 💬 技术支持

如果遇到问题，请检查：
1. 服务器日志输出
2. 数据库连接状态
3. 环境变量配置
4. Python版本兼容性

## 🎯 下一步

1. ✅ 启动服务器并运行测试
2. ✅ 通过 Swagger 文档熟悉API
3. ✅ 创建第一个项目
4. ✅ 尝试 SmartEye 下载和导入
5. 📝 开始前端集成

---

**🎉 祝你使用愉快！**
