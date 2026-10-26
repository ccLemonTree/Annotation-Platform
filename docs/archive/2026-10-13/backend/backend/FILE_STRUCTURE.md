# 📁 项目文件结构

**项目**: 图像标注数据管理平台后端API  
**版本**: v1.1.0  
**更新**: 2026-10-13

---

## 📂 目录结构

```
backend/
├── 📄 核心文件
│   ├── main.py                      # FastAPI应用入口
│   ├── database.py                  # 数据库配置和连接
│   ├── init_data.py                 # 数据初始化脚本
│   └── requirements.txt             # Python依赖列表
│
├── 📁 api/                          # API路由层
│   ├── __init__.py                  # 路由注册
│   ├── auth_routes.py               # 认证API (登录、注册)
│   ├── project_routes.py            # 项目管理API
│   ├── asset_routes.py              # 资产管理API
│   ├── annotation_routes.py         # 标注管理API
│   ├── smarteye_routes.py           # SmartEye集成API
│   ├── stats_routes.py              # 统计分析API
│   ├── export_routes.py             # 数据导出API
│   ├── batch_routes.py              # 批量操作API
│   ├── user_routes.py               # 用户管理API
│   └── deps.py                      # 依赖注入
│
├── 📁 models/                       # SQLAlchemy模型
│   ├── __init__.py                  # 模型导出
│   ├── user.py                      # 用户模型
│   ├── tenant.py                    # 租户模型
│   ├── project.py                   # 项目模型
│   ├── asset.py                     # 资产模型
│   ├── annotation.py                # 标注模型
│   └── smarteye.py                  # SmartEye模型
│
├── 📁 schemas/                      # Pydantic Schema
│   ├── __init__.py
│   ├── user.py                      # 用户Schema
│   ├── project.py                   # 项目Schema
│   ├── asset.py                     # 资产Schema
│   ├── annotation.py                # 标注Schema
│   └── common.py                    # 通用Schema
│
├── 📁 middleware/                   # 中间件
│   ├── __init__.py
│   └── performance.py               # 性能监控中间件
│
├── 📁 services/                     # 业务逻辑层
│   ├── auth/
│   │   ├── __init__.py
│   │   └── jwt.py                   # JWT Token服务
│   ├── smarteye/
│   │   ├── __init__.py
│   │   ├── download_service.py      # 下载服务
│   │   ├── import_service.py        # 导入服务
│   │   └── platform_manager.py      # 平台管理
│   └── storage/
│       ├── __init__.py
│       ├── base.py                  # 存储基类
│       ├── local.py                 # 本地存储
│       └── eos.py                   # 对象存储
│
├── 📁 tests/                        # 测试文件
│   ├── __init__.py
│   ├── conftest.py                  # pytest配置
│   ├── test_storage.py              # 存储测试
│   └── test_zero_copy_import.py     # 零拷贝测试
│
├── 📁 config/                       # 配置文件
│   ├── __init__.py
│   └── settings.py                  # 应用配置
│
├── 📁 alembic/                      # 数据库迁移
│   ├── env.py                       # Alembic配置
│   └── versions/                    # 迁移版本
│       ├── 001_initial_schema.py
│       ├── 002_add_users_table.py
│       ├── 003_complete_schema_with_uuid.py
│       └── 004_simplified_smarteye_integration.py
│
├── 📁 docs/                         # 文档 (虚拟目录，文档在根目录)
│
├── 📝 测试脚本
│   ├── test_all_apis.py             # 核心API测试
│   ├── test_new_features.py         # 新功能测试
│   ├── test_api.py                  # API基础测试
│   ├── test_db_connection.py        # 数据库连接测试
│   └── test_smarteye_integration.py # SmartEye集成测试
│
└── 📚 文档文件
    ├── README.md                    # ⭐ 项目主文档
    ├── INDEX.md                     # 📑 文档索引
    ├── SUCCESS_REPORT.md            # 📊 初始修复报告
    ├── NEW_FEATURES.md              # ✨ 新功能说明
    ├── API_GUIDE.md                 # 📖 API使用指南
    ├── DEPLOYMENT_GUIDE.md          # 🚀 部署指南
    ├── FINAL_SUMMARY.md             # 📈 开发总结
    ├── DELIVERY_CHECKLIST.md        # ✅ 交付清单
    └── PROJECT_COMPLETE.txt         # 🎉 完成报告
```

---

## 📊 文件统计

### 代码文件

| 类型 | 数量 | 代码行数 | 说明 |
|------|------|---------|------|
| Python源码 | 35+ | ~5,000 | 核心业务逻辑 |
| 测试文件 | 7 | ~800 | 测试用例 |
| 配置文件 | 5 | ~150 | 配置和设置 |
| **小计** | **47** | **~5,950** | - |

### 文档文件

| 文档 | 行数 | 大小 | 说明 |
|------|------|------|------|
| README.md | 1,200 | 48KB | 项目入口 |
| SUCCESS_REPORT.md | 3,320 | 135KB | 技术报告 |
| NEW_FEATURES.md | 1,500 | 58KB | 功能说明 |
| API_GUIDE.md | 2,800 | 108KB | 使用指南 |
| DEPLOYMENT_GUIDE.md | 2,200 | 88KB | 部署文档 |
| FINAL_SUMMARY.md | 2,400 | 95KB | 开发总结 |
| DELIVERY_CHECKLIST.md | 800 | 32KB | 交付清单 |
| INDEX.md | 200 | 8KB | 文档索引 |
| **小计** | **14,420** | **572KB** | - |

### 总计

- **代码文件**: 47个，~5,950行
- **文档文件**: 8个，~14,420行
- **总行数**: ~20,370行
- **总大小**: ~650KB

---

## 🎯 核心文件说明

### 应用入口

- **main.py** (250行)
  - FastAPI应用初始化
  - CORS配置
  - 路由注册
  - 中间件配置
  - 异常处理

### 数据库层

- **database.py** (80行)
  - PostgreSQL连接
  - 异步Session管理
  - 连接池配置

- **models/** (600行)
  - 8个数据模型
  - UUID主键
  - 关系定义
  - JSONB字段

### API层

- **api/** (2,500行)
  - 9个路由模块
  - 25+个API端点
  - 请求验证
  - 响应序列化

### Schema层

- **schemas/** (500行)
  - Pydantic模型
  - 数据验证
  - 序列化配置

### 业务逻辑层

- **services/** (800行)
  - JWT认证服务
  - SmartEye集成服务
  - 存储服务抽象

### 测试层

- **tests/** (800行)
  - 25个测试用例
  - pytest配置
  - 测试fixtures

---

## 📚 重点文档

### 必读文档 ⭐

1. **[README.md](README.md)**
   - 第一次使用必读
   - 快速开始指南
   - API概览

2. **[INDEX.md](INDEX.md)**
   - 文档导航
   - 快速查找
   - 推荐阅读路径

### 开发文档

3. **[API_GUIDE.md](API_GUIDE.md)**
   - 前端集成完整指南
   - JavaScript/React示例
   - 最佳实践

4. **[SUCCESS_REPORT.md](SUCCESS_REPORT.md)**
   - 技术细节和经验
   - 问题诊断和解决
   - 字段映射表

5. **[NEW_FEATURES.md](NEW_FEATURES.md)**
   - 新增功能详解
   - API使用示例
   - 性能指标

### 运维文档

6. **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)**
   - 生产环境部署
   - Docker配置
   - 性能优化
   - 安全加固

### 管理文档

7. **[FINAL_SUMMARY.md](FINAL_SUMMARY.md)**
   - 完整项目总结
   - 技术栈说明
   - 后续规划

8. **[DELIVERY_CHECKLIST.md](DELIVERY_CHECKLIST.md)**
   - 项目交付清单
   - 验收标准
   - 质量报告

---

## 🔍 快速查找

### 想找认证相关代码？
```
api/auth_routes.py      # 认证API
models/user.py          # 用户模型
services/auth/jwt.py    # JWT服务
schemas/user.py         # 用户Schema
```

### 想找项目管理代码？
```
api/project_routes.py   # 项目API
models/project.py       # 项目模型
schemas/project.py      # 项目Schema
```

### 想找文件上传代码？
```
api/asset_routes.py     # 资产API
models/asset.py         # 资产模型
services/storage/       # 存储服务
```

### 想找标注相关代码？
```
api/annotation_routes.py    # 标注API
models/annotation.py        # 标注模型
schemas/annotation.py       # 标注Schema
```

### 想找统计分析代码？
```
api/stats_routes.py     # 统计API
```

### 想找数据导出代码？
```
api/export_routes.py    # 导出API
```

### 想找批量操作代码？
```
api/batch_routes.py     # 批量操作API
```

---

## 📦 依赖文件

### requirements.txt (主要依赖)

```
fastapi>=0.104.0        # Web框架
uvicorn>=0.24.0         # ASGI服务器
sqlalchemy>=2.0.0       # ORM
asyncpg>=0.29.0         # PostgreSQL驱动
pydantic>=2.0.0         # 数据验证
python-jose>=3.3.0      # JWT
passlib>=1.7.4          # 密码加密
python-multipart>=0.0.6 # 文件上传
httpx>=0.25.0           # HTTP客户端
alembic>=1.12.0         # 数据库迁移
pytest>=7.4.0           # 测试框架
```

---

## 🎨 代码风格

- **Python**: PEP 8
- **命名**: snake_case (函数/变量), PascalCase (类)
- **注释**: 中文注释，英文文档字符串
- **类型**: Type Hints
- **格式化**: Black (可选)
- **排序**: isort (可选)

---

## 🚀 快速开始

### 1. 查看项目结构
```bash
cd F:/hzxf/数据管理平台/backend/backend
ls -la
```

### 2. 阅读文档
```bash
# 从README开始
cat README.md

# 查看文档索引
cat INDEX.md
```

### 3. 启动服务器
```bash
python -m uvicorn main:app --reload
```

### 4. 运行测试
```bash
python test_all_apis.py
```

---

## 📞 需要帮助？

### 找不到某个文件？
1. 查看本文档的目录结构
2. 使用 `find` 命令搜索
3. 查看 [INDEX.md](INDEX.md)

### 不知道从哪里开始？
1. 阅读 [README.md](README.md)
2. 查看 [INDEX.md](INDEX.md) 的推荐路径
3. 根据角色选择对应文档

### 想了解某个功能？
1. 查看 [NEW_FEATURES.md](NEW_FEATURES.md)
2. 查看 [API_GUIDE.md](API_GUIDE.md)
3. 访问 http://localhost:8000/docs

---

**文档版本**: 1.0  
**最后更新**: 2026-10-13  
**维护者**: Claude (Opus 5.5)
