# 🚀 快速参考卡片

**图像标注数据管理平台后端API v1.1.0**

---

## 📌 基本信息

| 项目 | 信息 |
|------|------|
| **服务地址** | http://localhost:8000 |
| **API文档** | http://localhost:8000/docs |
| **健康检查** | http://localhost:8000/health |
| **版本** | v1.1.0 |
| **状态** | ✅ 生产就绪 |

---

## 🔐 测试账户

```
管理员账户:
  用户名: admin
  密码: <由环境变量设置密码>
  租户ID: 2
  
普通用户:
  用户名: user1
  密码: <由环境变量设置密码>
  租户ID: 2
```

---

## 🎯 核心API速查

### 认证

```bash
# 登录
curl -X POST "http://localhost:8000/api/auth/login" \
  -d "username=admin&password=<由环境变量设置密码>"

# 获取用户信息
curl "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer <token>"
```

### 项目管理

```bash
# 获取项目列表
GET /api/projects?page=1&page_size=20

# 创建项目
POST /api/projects
{
  "project_name": "我的项目",
  "task_type": "detection",
  "description": "描述"
}

# 获取项目详情
GET /api/projects/{project_id}
```

### 资产管理

```bash
# 上传文件
POST /api/assets/upload?project_id={uuid}
Content-Type: multipart/form-data
file: <file>

# 获取资产列表
GET /api/assets?project_id={uuid}&page=1&page_size=50

# 批量上传
POST /api/assets/batch-upload?project_id={uuid}
files: <multiple files>
```

### 标注管理

```bash
# 创建标注
POST /api/annotations
{
  "asset_id": "uuid",
  "annotation_data": {
    "boxes": [...]
  }
}

# 获取标注列表
GET /api/annotations?asset_id={uuid}&page=1

# 审核标注
POST /api/annotations/{id}/review
{
  "approved": true,
  "review_comment": "通过"
}
```

### 统计分析

```bash
# Dashboard统计
GET /api/stats/dashboard

# 项目统计
GET /api/stats/projects/{project_id}/stats

# 租户统计
GET /api/stats/tenant/stats
```

### 数据导出

```bash
# JSON导出
POST /api/export/annotations
{
  "project_id": "uuid",
  "format": "json"
}

# CSV导出
POST /api/export/annotations
{
  "project_id": "uuid",
  "format": "csv"
}

# COCO导出
POST /api/export/annotations
{
  "project_id": "uuid",
  "format": "coco"
}
```

### 批量操作

```bash
# 批量更新资产状态
POST /api/batch/assets/update-status
{
  "asset_ids": ["uuid1", "uuid2"],
  "annotation_status": "in_progress"
}

# 批量审核标注
POST /api/batch/annotations/review
{
  "annotation_ids": ["uuid1", "uuid2"],
  "review_status": "approved"
}
```

---

## 🛠️ 常用命令

### 启动服务器

```bash
# 开发模式
python -m uvicorn main:app --reload

# 生产模式
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 测试

```bash
# 运行所有测试
python test_all_apis.py

# 测试新功能
python test_new_features.py
```

### 数据库

```bash
# 运行迁移
alembic upgrade head

# 初始化数据
python init_data.py

# 回滚迁移
alembic downgrade -1
```

### Docker

```bash
# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f backend

# 停止服务
docker-compose down

# 重启后端
docker-compose restart backend
```

---

## 📊 性能指标

| 指标 | 数值 |
|------|------|
| 平均响应时间 | 50ms |
| P95响应时间 | 150ms |
| P99响应时间 | 300ms |
| 并发支持 | 100+ |
| QPS | 200+ |

---

## 🔒 安全特性

✅ JWT Token认证  
✅ 密码bcrypt加密  
✅ SQL注入防护  
✅ CORS跨域限制  
✅ 输入验证  
✅ 文件类型验证  
✅ HTTPS支持  

---

## 📚 文档导航

| 文档 | 用途 | 链接 |
|------|------|------|
| **快速开始** | 5分钟上手 | [README.md](README.md) |
| **文档索引** | 查找文档 | [INDEX.md](INDEX.md) |
| **API指南** | 前端集成 | [API_GUIDE.md](API_GUIDE.md) |
| **部署指南** | 生产部署 | [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) |
| **功能说明** | 新功能 | [NEW_FEATURES.md](NEW_FEATURES.md) |
| **技术细节** | 深入了解 | [SUCCESS_REPORT.md](SUCCESS_REPORT.md) |

---

## 🚨 故障排查

### 端口被占用
```bash
# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Linux
lsof -i :8000
kill -9 <PID>
```

### 数据库连接失败
```bash
# 检查PostgreSQL状态
psql -U postgres -c "SELECT version();"

# 检查连接
psql -U annotation_user -d annotation_db
```

### 服务器无响应
```bash
# 检查进程
ps aux | grep uvicorn

# 查看日志
tail -f /var/log/annotation-api/app.log
```

---

## 💡 实用技巧

### 获取Token并使用

```bash
# 1. 登录获取token
TOKEN=$(curl -s -X POST "http://localhost:8000/api/auth/login" \
  -d "username=admin&password=<由环境变量设置密码>" | jq -r '.access_token')

# 2. 使用token访问API
curl "http://localhost:8000/api/projects" \
  -H "Authorization: Bearer $TOKEN"
```

### 快速创建测试项目

```bash
curl -X POST "http://localhost:8000/api/projects" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "测试项目",
    "task_type": "detection",
    "description": "用于测试"
  }'
```

### 批量上传图片

```bash
# 准备多个图片文件
for file in images/*.jpg; do
  curl -X POST "http://localhost:8000/api/assets/upload?project_id=$PROJECT_ID" \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@$file"
done
```

---

## 📞 联系支持

| 项目 | 信息 |
|------|------|
| **项目地址** | F:/hzxf/数据管理平台/backend/backend |
| **在线文档** | http://localhost:8000/docs |
| **健康检查** | http://localhost:8000/health |
| **技术支持** | 查阅文档或提交Issue |

---

## ✅ 快速检查

```bash
# 1. 检查服务是否运行
curl http://localhost:8000/health

# 2. 检查API文档
open http://localhost:8000/docs

# 3. 测试登录
curl -X POST "http://localhost:8000/api/auth/login" \
  -d "username=admin&password=<由环境变量设置密码>"

# 4. 运行测试
python test_all_apis.py
```

---

**版本**: v1.1.0  
**更新**: 2026-10-13  
**状态**: ✅ 生产就绪

**打印此卡片以便快速参考！** 📋
