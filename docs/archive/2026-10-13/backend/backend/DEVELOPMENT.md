# 开发者快速参考

## 快速开始

### Windows
```bash
# 运行启动脚本
start.bat
```

### Linux/Mac
```bash
# 给脚本添加执行权限（首次）
chmod +x start.sh

# 运行启动脚本
./start.sh
```

### 手动启动
```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 激活虚拟环境
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件

# 5. 初始化数据库
python init_db.py

# 6. 启动服务
uvicorn main:app --reload
```

## 数据库操作

### 初始化数据库（首次）
```bash
# 创建默认租户和管理员
python init_db.py
```

### 数据库迁移
```bash
# 生成迁移文件
alembic revision --autogenerate -m "描述你的变更"

# 应用迁移
alembic upgrade head

# 回滚迁移
alembic downgrade -1

# 查看迁移历史
alembic history

# 查看当前版本
alembic current
```

### 测试数据库连接
```bash
python test_db_connection.py
```

## Docker开发

### 启动所有服务
```bash
docker-compose up -d
```

### 查看日志
```bash
# 所有服务
docker-compose logs -f

# 仅后端
docker-compose logs -f backend

# 仅数据库
docker-compose logs -f postgres
```

### 停止服务
```bash
docker-compose down

# 同时删除卷（数据会丢失）
docker-compose down -v
```

### 重启服务
```bash
docker-compose restart backend
```

### 进入容器
```bash
# 进入后端容器
docker-compose exec backend bash

# 进入数据库容器
docker-compose exec postgres psql -U postgres -d data_platform
```

## API测试

### 使用curl

#### 注册用户
```bash
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com",
    "password": "password123",
    "full_name": "测试用户"
  }'
```

#### 登录
```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=<由环境变量设置密码>"
```

#### 获取当前用户信息
```bash
# 先登录获取token
TOKEN="your-jwt-token-here"

curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer $TOKEN"
```

#### 创建项目
```bash
curl -X POST "http://localhost:8000/api/projects" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "测试项目",
    "description": "这是一个测试项目",
    "task_type": "detection",
    "class_labels": ["cat", "dog", "person"]
  }'
```

#### 上传资产
```bash
curl -X POST "http://localhost:8000/api/assets/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/image.jpg" \
  -F "project_id=1"
```

### 使用httpie（更友好）

```bash
# 安装httpie
pip install httpie

# 登录
http POST http://localhost:8000/api/auth/login \
  username=admin password=<由环境变量设置密码>

# 获取用户信息
http GET http://localhost:8000/api/auth/me \
  "Authorization: Bearer $TOKEN"

# 创建项目
http POST http://localhost:8000/api/projects \
  "Authorization: Bearer $TOKEN" \
  name="测试项目" \
  task_type="detection" \
  class_labels:='["cat", "dog"]'
```

## 常见问题

### 1. 数据库连接失败

**问题**: `sqlalchemy.exc.OperationalError: could not connect to server`

**解决**:
- 检查PostgreSQL是否运行: `docker-compose ps`
- 检查.env中的DATABASE_URL配置
- 确保端口5432未被占用

### 2. 导入错误

**问题**: `ModuleNotFoundError: No module named 'xxx'`

**解决**:
```bash
# 确保虚拟环境已激活
# 重新安装依赖
pip install -r requirements.txt
```

### 3. Alembic迁移失败

**问题**: 迁移文件生成失败或应用失败

**解决**:
```bash
# 检查数据库连接
python test_db_connection.py

# 删除alembic/versions/中的错误迁移文件
# 重新生成
alembic revision --autogenerate -m "message"
```

### 4. 端口被占用

**问题**: `OSError: [Errno 98] Address already in use`

**解决**:
```bash
# Windows - 查找占用8000端口的进程
netstat -ano | findstr :8000
# 杀死进程
taskkill /PID <进程ID> /F

# Linux/Mac
lsof -ti:8000 | xargs kill -9

# 或使用其他端口
uvicorn main:app --reload --port 8001
```

## 代码风格

### 格式化代码
```bash
# 安装格式化工具
pip install black isort

# 格式化代码
black .

# 排序导入
isort .
```

### 代码检查
```bash
# 安装检查工具
pip install flake8 mypy

# 运行检查
flake8 .
mypy .
```

## 日志

### 查看日志
```bash
# 实时查看应用日志
tail -f logs/app.log

# 查看错误日志
tail -f logs/error.log
```

### 配置日志级别

在 `.env` 中设置:
```env
LOG_LEVEL=DEBUG  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

## 性能监控

### 查看API响应时间
访问 `/docs` 页面，每个API都有响应时间显示

### 数据库查询优化
在 `config/database.py` 中设置 `echo=True` 查看SQL语句

## 部署检查清单

- [ ] 修改 `.env` 中的 `JWT_SECRET_KEY`
- [ ] 修改默认管理员密码
- [ ] 设置 `DEBUG=False`
- [ ] 配置CORS允许的域名
- [ ] 设置合适的文件上传大小限制
- [ ] 配置数据库备份策略
- [ ] 设置日志轮转
- [ ] 配置HTTPS
- [ ] 设置防火墙规则

## 有用的命令

```bash
# 查看项目结构
tree -I 'venv|__pycache__|*.pyc'

# 统计代码行数
find . -name "*.py" | xargs wc -l

# 查看Python包信息
pip show fastapi

# 导出当前环境的依赖
pip freeze > requirements.txt

# 查看进程
ps aux | grep uvicorn

# 查看端口占用
netstat -tulpn | grep 8000
```
