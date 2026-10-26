# 🗄️ 数据库操作指南

数据库迁移、查询和维护的完整指南。

## 数据库信息

**连接信息**：
- 主机: `192.168.1.115`
- 端口: `5432`
- 数据库: `postgres`
- 用户: `postgres`
- 密码: `123456`

**连接字符串**：
```
postgresql://postgres:123456@192.168.1.115:5432/postgres
```

## Alembic 迁移管理

### 常用命令

```bash
# 查看当前版本
alembic current

# 查看迁移历史
alembic history --verbose

# 创建新迁移（自动检测模型变更）
alembic revision --autogenerate -m "描述变更内容"

# 手动创建空白迁移
alembic revision -m "描述变更内容"

# 升级到最新版本
alembic upgrade head

# 升级到指定版本
alembic upgrade <revision_id>

# 升级一个版本
alembic upgrade +1

# 回滚一个版本
alembic downgrade -1

# 回滚到指定版本
alembic downgrade <revision_id>

# 回滚所有迁移
alembic downgrade base

# 查看SQL（不执行）
alembic upgrade head --sql
```

### 创建迁移的工作流程

#### 1. 修改模型

编辑 `models/` 中的模型文件：

```python
# models/project.py
class Project(Base):
    __tablename__ = "projects"
    
    # 添加新字段
    priority = Column(Integer, default=0, comment="优先级")
```

#### 2. 生成迁移

```bash
alembic revision --autogenerate -m "add priority to projects"
```

#### 3. 检查生成的迁移文件

```bash
# 文件位置: alembic/versions/<timestamp>_add_priority_to_projects.py
cat alembic/versions/<最新文件>
```

检查要点：
- ✅ 升级操作 (upgrade) 正确
- ✅ 降级操作 (downgrade) 正确
- ✅ 没有意外的表或列变更
- ✅ 外键约束正确

#### 4. 应用迁移

```bash
alembic upgrade head
```

#### 5. 验证变更

```bash
# 连接数据库查看
psql -h 192.168.1.115 -U postgres -d postgres

# 查看表结构
\d+ projects

# 退出
\q
```

### 常见迁移场景

#### 添加新表

```python
# upgrade()
op.create_table(
    'new_table',
    sa.Column('id', sa.Integer(), primary_key=True),
    sa.Column('name', sa.String(255), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
)

# downgrade()
op.drop_table('new_table')
```

#### 添加列

```python
# upgrade()
op.add_column('projects', sa.Column('priority', sa.Integer(), default=0))

# downgrade()
op.drop_column('projects', 'priority')
```

#### 修改列

```python
# upgrade()
op.alter_column('projects', 'name',
    existing_type=sa.String(100),
    type_=sa.String(255),
    nullable=False
)

# downgrade()
op.alter_column('projects', 'name',
    existing_type=sa.String(255),
    type_=sa.String(100),
    nullable=True
)
```

#### 添加索引

```python
# upgrade()
op.create_index('ix_projects_status', 'projects', ['status'])

# downgrade()
op.drop_index('ix_projects_status', 'projects')
```

#### 添加外键

```python
# upgrade()
op.create_foreign_key(
    'fk_projects_tenant_id',
    'projects', 'tenants',
    ['tenant_id'], ['id'],
    ondelete='CASCADE'
)

# downgrade()
op.drop_constraint('fk_projects_tenant_id', 'projects', type_='foreignkey')
```

#### 创建 ENUM 类型

```python
# upgrade()
task_type = postgresql.ENUM('DETECTION', 'CLASSIFICATION', 'SEGMENTATION', 'OCR', name='tasktype')
task_type.create(op.get_bind())
op.add_column('projects', sa.Column('task_type', task_type, nullable=False))

# downgrade()
op.drop_column('projects', 'task_type')
op.execute('DROP TYPE tasktype')
```

## 直接数据库操作

### 使用 psql

```bash
# 连接数据库
psql -h 192.168.1.115 -U postgres -d postgres

# 或使用完整连接串
psql "postgresql://postgres:123456@192.168.1.115:5432/postgres"
```

### 常用 SQL 命令

```sql
-- 查看所有表
\dt

-- 查看表结构
\d+ projects

-- 查看表数据量
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
    pg_total_relation_size(schemaname||'.'||tablename) AS size_bytes
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY size_bytes DESC;

-- 查看索引
\di

-- 查看外键
SELECT
    tc.table_name, 
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name 
FROM information_schema.table_constraints AS tc 
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY';

-- 查看 ENUM 类型
SELECT 
    t.typname AS enum_name,
    string_agg(e.enumlabel, ', ' ORDER BY e.enumsortorder) AS enum_values
FROM pg_type t 
JOIN pg_enum e ON t.oid = e.enumtypid  
JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public'
GROUP BY t.typname;
```

### 查询数据

```sql
-- 查看租户
SELECT * FROM tenants;

-- 查看项目
SELECT id, name, task_type, status, total_assets, created_at 
FROM projects 
ORDER BY created_at DESC 
LIMIT 10;

-- 查看标注统计
SELECT 
    p.name AS project_name,
    COUNT(a.id) AS total_annotations,
    COUNT(a.id) FILTER (WHERE a.status = 'ANNOTATED') AS annotated,
    COUNT(a.id) FILTER (WHERE a.status = 'REVIEWED') AS reviewed
FROM projects p
LEFT JOIN assets ast ON ast.project_id = p.id
LEFT JOIN annotations a ON a.asset_id = ast.id
GROUP BY p.id, p.name;

-- 查看存储使用
SELECT 
    project_id,
    COUNT(*) AS asset_count,
    SUM(file_size) AS total_size,
    pg_size_pretty(SUM(file_size)::bigint) AS total_size_pretty
FROM assets
GROUP BY project_id;
```

### 数据维护

```sql
-- 清理测试数据
DELETE FROM annotations WHERE asset_id IN (SELECT id FROM assets WHERE project_id = 1);
DELETE FROM assets WHERE project_id = 1;
DELETE FROM projects WHERE id = 1;

-- 重置自增ID（小心使用！）
ALTER SEQUENCE projects_id_seq RESTART WITH 1;

-- 更新统计信息
UPDATE projects p SET 
    total_assets = (SELECT COUNT(*) FROM assets WHERE project_id = p.id),
    annotated_count = (SELECT COUNT(*) FROM annotations WHERE asset_id IN (SELECT id FROM assets WHERE project_id = p.id) AND status = 'ANNOTATED'),
    reviewed_count = (SELECT COUNT(*) FROM annotations WHERE asset_id IN (SELECT id FROM assets WHERE project_id = p.id) AND status = 'REVIEWED');

-- 真空清理
VACUUM ANALYZE projects;
```

## Python 脚本操作

### 创建管理脚本

创建 `scripts/db_management.py`：

```python
"""
数据库管理脚本
"""
import asyncio
from sqlalchemy import text
from config.database import get_db, engine
from models import *


async def show_tables():
    """显示所有表"""
    async for db in get_db():
        result = await db.execute(text("""
            SELECT table_name, 
                   pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as size
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name
        """))
        
        print("\n📊 数据库表:")
        print("-" * 60)
        for row in result:
            print(f"  {row.table_name:<30} {row.size:>10}")
        break


async def show_stats():
    """显示统计信息"""
    async for db in get_db():
        # 项目统计
        result = await db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'ACTIVE') as active,
                COUNT(*) FILTER (WHERE status = 'PAUSED') as paused
            FROM projects
        """))
        stats = result.fetchone()
        
        print("\n📈 项目统计:")
        print(f"  总项目: {stats.total}")
        print(f"  活跃: {stats.active}")
        print(f"  暂停: {stats.paused}")
        
        break


async def create_test_data():
    """创建测试数据"""
    async for db in get_db():
        # 创建测试租户
        from models.tenant import Tenant
        tenant = Tenant(name="测试租户", code="test")
        db.add(tenant)
        await db.commit()
        
        # 创建测试项目
        from models.project import Project, TaskType
        project = Project(
            tenant_id=tenant.id,
            name="测试项目",
            task_type=TaskType.DETECTION,
            classes=["person", "car", "dog"]
        )
        db.add(project)
        await db.commit()
        
        print(f"✅ 创建测试数据: 租户ID={tenant.id}, 项目ID={project.id}")
        break


async def cleanup_test_data():
    """清理测试数据"""
    async for db in get_db():
        await db.execute(text("DELETE FROM projects WHERE name LIKE '测试%'"))
        await db.execute(text("DELETE FROM tenants WHERE code = 'test'"))
        await db.commit()
        print("✅ 清理完成")
        break


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python scripts/db_management.py [command]")
        print("命令:")
        print("  tables    - 显示所有表")
        print("  stats     - 显示统计信息")
        print("  testdata  - 创建测试数据")
        print("  cleanup   - 清理测试数据")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "tables":
        asyncio.run(show_tables())
    elif command == "stats":
        asyncio.run(show_stats())
    elif command == "testdata":
        asyncio.run(create_test_data())
    elif command == "cleanup":
        asyncio.run(cleanup_test_data())
    else:
        print(f"未知命令: {command}")
```

使用：

```bash
# 显示所有表
python scripts/db_management.py tables

# 显示统计
python scripts/db_management.py stats

# 创建测试数据
python scripts/db_management.py testdata

# 清理测试数据
python scripts/db_management.py cleanup
```

## 备份与恢复

### 备份数据库

```bash
# 备份整个数据库
pg_dump -h 192.168.1.115 -U postgres -d postgres > backup_$(date +%Y%m%d).sql

# 只备份结构
pg_dump -h 192.168.1.115 -U postgres -d postgres --schema-only > schema_backup.sql

# 只备份数据
pg_dump -h 192.168.1.115 -U postgres -d postgres --data-only > data_backup.sql

# 备份特定表
pg_dump -h 192.168.1.115 -U postgres -d postgres -t projects -t assets > tables_backup.sql

# 压缩备份
pg_dump -h 192.168.1.115 -U postgres -d postgres | gzip > backup.sql.gz
```

### 恢复数据库

```bash
# 恢复整个数据库
psql -h 192.168.1.115 -U postgres -d postgres < backup.sql

# 恢复压缩备份
gunzip -c backup.sql.gz | psql -h 192.168.1.115 -U postgres -d postgres

# 只恢复特定表
pg_restore -h 192.168.1.115 -U postgres -d postgres -t projects backup.sql
```

## 性能优化

### 查询慢查询

```sql
-- 启用慢查询日志（需要超级用户权限）
ALTER SYSTEM SET log_min_duration_statement = 1000; -- 记录超过1秒的查询
SELECT pg_reload_conf();

-- 查看当前运行的查询
SELECT pid, usename, application_name, state, query
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY query_start;

-- 杀死长时间运行的查询
SELECT pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE state = 'active' AND query_start < NOW() - INTERVAL '5 minutes';
```

### 查看索引使用情况

```sql
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan AS index_scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC;
```

### 分析表

```sql
-- 更新统计信息
ANALYZE projects;

-- 完整分析
VACUUM FULL ANALYZE projects;
```

## 故障排查

### 连接问题

```bash
# 测试连接
psql -h 192.168.1.115 -U postgres -d postgres -c "SELECT version();"

# 检查连接数
psql -h 192.168.1.115 -U postgres -d postgres -c "SELECT COUNT(*) FROM pg_stat_activity;"
```

### 锁问题

```sql
-- 查看锁
SELECT 
    l.pid,
    l.mode,
    l.granted,
    a.query
FROM pg_locks l
JOIN pg_stat_activity a ON l.pid = a.pid
WHERE NOT l.granted;

-- 查看阻塞关系
SELECT 
    blocked.pid AS blocked_pid,
    blocked.query AS blocked_query,
    blocking.pid AS blocking_pid,
    blocking.query AS blocking_query
FROM pg_stat_activity blocked
JOIN pg_locks blocked_locks ON blocked.pid = blocked_locks.pid
JOIN pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
JOIN pg_stat_activity blocking ON blocking_locks.pid = blocking.pid
WHERE NOT blocked_locks.granted AND blocking_locks.granted;
```

## 最佳实践

1. **始终使用迁移**: 不要手动修改生产数据库
2. **先在开发环境测试**: 测试迁移后再应用到生产
3. **定期备份**: 每天自动备份，重大变更前手动备份
4. **监控性能**: 定期检查慢查询和索引使用
5. **版本控制**: 所有迁移文件纳入Git
6. **文档记录**: 重要的数据变更要有文档记录

---

**警告**: 生产环境操作需谨慎，建议先在开发环境验证。
