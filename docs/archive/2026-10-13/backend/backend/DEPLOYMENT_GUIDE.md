# 🚀 部署指南

**项目**: 图像标注数据管理平台后端  
**版本**: v1.1.0  
**更新**: 2026-10-13

---

## 📋 系统要求

### 硬件要求
- **CPU**: 2核及以上
- **内存**: 4GB及以上（推荐8GB）
- **硬盘**: 20GB可用空间（不含数据存储）
- **网络**: 稳定的互联网连接

### 软件要求
- **操作系统**: 
  - Linux (Ubuntu 20.04+, CentOS 8+)
  - Windows 10/11
  - macOS 11+
- **Python**: 3.11+
- **PostgreSQL**: 14+
- **Nginx**: 1.18+ (生产环境)

---

## 🔧 开发环境部署

### 1. 安装依赖

```bash
# 克隆项目
cd F:/hzxf/数据管理平台/backend/backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置数据库

```bash
# 安装PostgreSQL后，创建数据库
psql -U postgres
CREATE DATABASE annotation_db;
CREATE USER annotation_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE annotation_db TO annotation_user;
\q
```

### 3. 配置环境变量

创建 `.env` 文件：

```bash
# .env
DATABASE_URL=postgresql+asyncpg://annotation_user:your_password@localhost/annotation_db
SECRET_KEY=your-secret-key-here-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# 文件存储配置
UPLOAD_DIR=./uploads
MAX_UPLOAD_SIZE=10485760  # 10MB

# CORS配置
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
```

### 4. 初始化数据库

```bash
# 运行迁移
alembic upgrade head

# 初始化测试数据
python init_data.py
```

### 5. 启动服务器

```bash
# 开发模式（自动重载）
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 服务器启动后访问：
# - API: http://localhost:8000
# - 文档: http://localhost:8000/docs
```

---

## 🏭 生产环境部署

### 方案1: Docker部署（推荐）

#### 1.1 创建Dockerfile

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建上传目录
RUN mkdir -p uploads

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

#### 1.2 创建docker-compose.yml

```yaml
# docker-compose.yml
version: '3.8'

services:
  db:
    image: postgres:14
    environment:
      POSTGRES_DB: annotation_db
      POSTGRES_USER: annotation_user
      POSTGRES_PASSWORD: your_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U annotation_user"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build: .
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://annotation_user:your_password@db/annotation_db
      SECRET_KEY: your-secret-key-here
      ALGORITHM: HS256
      ACCESS_TOKEN_EXPIRE_MINUTES: 60
    volumes:
      - ./uploads:/app/uploads
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./uploads:/usr/share/nginx/html/uploads
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  postgres_data:
```

#### 1.3 部署命令

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f backend

# 停止服务
docker-compose down

# 重启服务
docker-compose restart backend
```

### 方案2: Systemd服务部署

#### 2.1 创建服务文件

```bash
# /etc/systemd/system/annotation-api.service
[Unit]
Description=Annotation Platform API
After=network.target postgresql.service

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/var/www/annotation-backend
Environment="PATH=/var/www/annotation-backend/venv/bin"
ExecStart=/var/www/annotation-backend/venv/bin/uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --log-config logging.json

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 2.2 启动服务

```bash
# 重载systemd配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start annotation-api

# 设置开机自启
sudo systemctl enable annotation-api

# 查看状态
sudo systemctl status annotation-api

# 查看日志
sudo journalctl -u annotation-api -f
```

### 方案3: Supervisor部署

#### 3.1 安装Supervisor

```bash
sudo apt-get install supervisor
```

#### 3.2 创建配置文件

```ini
# /etc/supervisor/conf.d/annotation-api.conf
[program:annotation-api]
command=/var/www/annotation-backend/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
directory=/var/www/annotation-backend
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/annotation-api.log
stderr_logfile=/var/log/annotation-api-error.log
```

#### 3.3 启动服务

```bash
# 重载配置
sudo supervisorctl reread
sudo supervisorctl update

# 启动服务
sudo supervisorctl start annotation-api

# 查看状态
sudo supervisorctl status annotation-api
```

---

## 🌐 Nginx配置

### 基础配置

```nginx
# /etc/nginx/sites-available/annotation-api
server {
    listen 80;
    server_name api.yourdomain.com;

    client_max_body_size 100M;

    # API代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket支持（如果需要）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # 静态文件（上传的图片）
    location /uploads/ {
        alias /var/www/annotation-backend/uploads/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # API文档
    location /docs {
        proxy_pass http://127.0.0.1:8000/docs;
        proxy_set_header Host $host;
    }

    # 健康检查
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        access_log off;
    }
}
```

### HTTPS配置（使用Let's Encrypt）

```bash
# 安装Certbot
sudo apt-get install certbot python3-certbot-nginx

# 获取证书
sudo certbot --nginx -d api.yourdomain.com

# 自动续期
sudo certbot renew --dry-run
```

自动生成的HTTPS配置：

```nginx
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # 其他配置同上...
}

# HTTP重定向到HTTPS
server {
    listen 80;
    server_name api.yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

---

## 📊 性能优化

### 1. 数据库优化

```sql
-- 创建索引
CREATE INDEX idx_assets_project_id ON assets(project_id);
CREATE INDEX idx_assets_status ON assets(annotation_status);
CREATE INDEX idx_annotations_asset_id ON annotations(asset_id);
CREATE INDEX idx_projects_tenant_id ON projects(tenant_id);

-- 启用连接池
-- 在DATABASE_URL中添加：
?pool_size=20&max_overflow=10
```

### 2. 应用配置

```python
# main.py
app = FastAPI(
    title="Annotation Platform API",
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    
    # 生产环境关闭文档
    # docs_url=None,
    # redoc_url=None,
)

# 启用Gzip压缩
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 限流中间件
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

### 3. Worker配置

```bash
# 根据CPU核心数配置worker
# workers = (2 × CPU核心数) + 1

# 4核CPU推荐配置
uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 9 \
    --worker-class uvicorn.workers.UvicornWorker \
    --log-level info
```

---

## 🔒 安全加固

### 1. 环境变量管理

```bash
# 使用环境变量而不是.env文件
export DATABASE_URL="postgresql+asyncpg://..."
export SECRET_KEY="$(openssl rand -hex 32)"
export ALLOWED_ORIGINS="https://yourdomain.com"
```

### 2. 防火墙配置

```bash
# UFW (Ubuntu)
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable

# 限制数据库访问
sudo ufw deny 5432/tcp
```

### 3. 限流配置

```python
# api/deps.py
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)

@router.post("/login")
@limiter.limit("5/minute")  # 限制登录频率
async def login(...):
    ...
```

### 4. CORS严格配置

```python
# 生产环境只允许特定域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
```

---

## 📈 监控与日志

### 1. 日志配置

```json
// logging.json
{
  "version": 1,
  "disable_existing_loggers": false,
  "formatters": {
    "default": {
      "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    },
    "json": {
      "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
      "format": "%(asctime)s %(name)s %(levelname)s %(message)s"
    }
  },
  "handlers": {
    "console": {
      "class": "logging.StreamHandler",
      "formatter": "default"
    },
    "file": {
      "class": "logging.handlers.RotatingFileHandler",
      "filename": "/var/log/annotation-api/app.log",
      "maxBytes": 10485760,
      "backupCount": 5,
      "formatter": "json"
    }
  },
  "root": {
    "level": "INFO",
    "handlers": ["console", "file"]
  }
}
```

### 2. 健康检查端点

```python
# main.py
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.1.0",
        "timestamp": datetime.utcnow().isoformat()
    }

# 数据库健康检查
@app.get("/health/db")
async def db_health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
```

### 3. Prometheus监控

```python
# 安装依赖
pip install prometheus-fastapi-instrumentator

# main.py
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)

# 访问指标: http://localhost:8000/metrics
```

---

## 🔄 备份策略

### 1. 数据库备份

```bash
# 创建备份脚本
cat > /usr/local/bin/backup-db.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/var/backups/annotation-db"
DATE=$(date +%Y%m%d_%H%M%S)
FILENAME="annotation_db_${DATE}.sql.gz"

mkdir -p $BACKUP_DIR
pg_dump -U annotation_user annotation_db | gzip > $BACKUP_DIR/$FILENAME

# 保留最近7天的备份
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

echo "Backup completed: $FILENAME"
EOF

chmod +x /usr/local/bin/backup-db.sh

# 配置定时任务
crontab -e
# 每天凌晨2点备份
0 2 * * * /usr/local/bin/backup-db.sh
```

### 2. 文件备份

```bash
# 备份上传的文件
rsync -avz --delete /var/www/annotation-backend/uploads/ \
    /backup/uploads/
```

---

## 🚨 故障排查

### 常见问题

#### 1. 数据库连接失败

```bash
# 检查PostgreSQL状态
sudo systemctl status postgresql

# 检查连接
psql -U annotation_user -d annotation_db -h localhost

# 查看日志
sudo tail -f /var/log/postgresql/postgresql-14-main.log
```

#### 2. 端口被占用

```bash
# 查找占用端口的进程
lsof -i :8000
# 或
netstat -tulpn | grep 8000

# 杀死进程
kill -9 <PID>
```

#### 3. 权限问题

```bash
# 确保uploads目录可写
sudo chown -R www-data:www-data /var/www/annotation-backend/uploads
sudo chmod -R 755 /var/www/annotation-backend/uploads
```

#### 4. 内存不足

```bash
# 查看内存使用
free -h

# 添加swap
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

---

## 📦 更新部署

### 1. 零停机更新

```bash
# 使用Nginx + 多个后端实例
# nginx.conf
upstream backend {
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
}

# 更新流程
# 1. 更新代码
git pull origin main

# 2. 重启第一个实例
sudo systemctl restart annotation-api-8000

# 3. 等待启动完成
sleep 5

# 4. 重启第二个实例
sudo systemctl restart annotation-api-8001
```

### 2. 数据库迁移

```bash
# 1. 备份数据库
./backup-db.sh

# 2. 运行迁移
alembic upgrade head

# 3. 验证
python -c "
from database import engine
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text('SELECT version_num FROM alembic_version'))
    print('Current version:', result.scalar())
"
```

---

## 📞 技术支持

- **文档**: http://localhost:8000/docs
- **监控**: http://localhost:8000/metrics
- **健康检查**: http://localhost:8000/health

---

**更新日期**: 2026-10-13  
**维护者**: DevOps Team
