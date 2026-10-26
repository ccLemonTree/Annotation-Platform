# P0 数据库收尾证据（2026-10-15）

本次只修改训练 ORM、013、独立初始化脚本及专属测试/证据。014 与模型配置 API 由父代理负责。没有启动或重启服务，没有创建提交，没有迁移业务库或删除业务数据。

## 交付文件

- `backend/backend/models/training.py`：`TrainingTask.project_id`、`Model.project_id` 改为 `Mapped[uuid.UUID]` / PostgreSQL UUID，与 `projects.id` 一致。
- `backend/backend/alembic/versions/013_restore_training_tables.py`：冻结 schema，`012 -> 013`；补齐 `training_tasks`、`training_metrics`、`models`。先检查所有已有表，再创建缺失表。对列/类型/空值约束、自增 ID、时间默认值、主键、外键、索引和状态枚举的不兼容情况明确报错，不自动改写旧数据。降级拒绝删除训练数据。
- `backend/backend/scripts/bootstrap_database.py`：必须显式提供目标 URL，只初始化已有空数据库；创建当前核心 ORM 表（含 `model_services`）、SmartEye runtime 三表和 `smarteye` schema 八表。排除旧 `smarteye_tasks` / `smarteye_downloads` ORM 表。DDL 与动态 head stamp 在同一事务内完成；当前 head 为 `014`。
- `backend/backend/tests/test_database_closeout_postgres.py`：仅操作本 fixture 创建的随机数据库；专属临时目录与并行测试隔离。
- `docs/testing/p0-database-closeout-2026-10-15.xml`：最终 pytest JUnit 原始结果，每个 PG 用例记录临时库名、创建时 OID、删除验证结果。

## 旧迁移核查与初始化方案

001 是 BigInteger 版初始 schema；002 在其上创建用户表。003 含 `DROP TABLE ... CASCADE`、`DROP TYPE ... CASCADE`，删去训练三表后未重建；004 也会删除旧 SmartEye 表。因此不能把重放 001—004 作为当前新库初始化入口，更不能在业务库上重跑。

新脚本直接创建当前 schema，不执行旧迁移，不创建默认账号，不调用原有 `init_db.py` / `init_database.py`，也不创建或删除数据库。脚本通过 `MigrationContext` 在当前连接内准确 stamp 当前唯一 head，不经过会回退到应用配置的 Alembic env。未来新增迁移时，需保证发布的 ORM/SmartEye schema 与该 head 同步。

仅针对预先创建的全新空库使用：

父代理整合补充：脚本现支持`--admin-username`和`--admin-email`，同时必须设置`BOOTSTRAP_ADMIN_PASSWORD`环境变量，密码按现有账号规则验证并哈希。默认租户和管理员在同一初始化事务创建；不带管理员选项时仍只建schema。已有库始终拒绝初始化，因此不会重置已有密码或新增重复管理员。账号创建和缺失密码拒绝另有2项回归；与原空库CLI兼容用例一起复跑3项通过，未创建真实业务账号。

```powershell
Set-Location F:/hzxf/数据管理平台/backend/backend
# URL 必须指向父代理/管理员已创建的全新库；凭据使用实际环境安全注入。
$env:DATABASE_BOOTSTRAP_URL = 'postgresql+psycopg2://USER:PASSWORD@HOST:5432/NEW_EMPTY_DATABASE'
./.venv/Scripts/python.exe ./scripts/bootstrap_database.py
Remove-Item Env:/DATABASE_BOOTSTRAP_URL
```

脚本遇到用户 schema 内已有表/视图/序列/独立类型会拒绝，不接受“只看 public 为空”。也测试了 `pgapp` 用户 schema，避免把 SQL LIKE 中的 `_` 当作字面下划线而漏检。

## 红绿证据与复跑

真实 PostgreSQL：`192.168.1.115:5432`，PostgreSQL `16.11`。只通过管理连接创建本次 `codex_p0_db_<uuid>` 数据库，不检查或修改业务表。

初始回归测试实跑：`20 failed`；明确观察到 ORM BigInteger/UUID 外键不兼容、缺少 revision 013、缺少安全初始化脚本。父代理增加 014 后，新增初始化测试再次观察到漏建 `model_services`、误建两张旧 SmartEye 表。非空 `pgapp` schema 用例也先失败（未拒绝），修正检查表达式后复跑。

最终专项结果：**23 passed, 37 warnings in 96.09s**（exit code 0）。JUnit 记录 20 个本轮临时库，每个库都有 OID 与 `cleanup_verified=true`；另三个用例不需要数据库。测试结束后再次按 JUnit 中的精确库名查询 `pg_database`，残留为 0。Python `compileall` 对四个本次 Python 文件检查通过。

复跑命令（唯一测试启用项是已有 `P0_POSTGRES_INTEGRATION=1`）：

```powershell
Set-Location F:/hzxf/数据管理平台/backend/backend
$env:P0_POSTGRES_INTEGRATION = '1'
./.venv/Scripts/python.exe -m pytest tests/test_database_closeout_postgres.py -q --basetemp=.pytest-p0-database --tb=short --junitxml=../../docs/testing/p0-database-closeout-2026-10-15.xml
```

以上是本轮已执行命令，使用的临时目录全程独立于共享 `.pytest-tmp`。父代理随后统一约定目录命名，后续重跑请把参数改为 `--basetemp=.pytest-tmp-db`；不需要修改 pytest.ini。

验证覆盖：

- `012 -> 013` 实际 Alembic upgrade，三张表的列、索引、外键；UUID 项目下真实写入任务、指标、模型。
- 已有业务项目/租户与兼容训练记录保留；部分表存在时只补缺失表；反复 upgrade 不丢行；downgrade 明确拒绝。
- BigInteger 项目外键、缺列、窄字符串类型、错误可空性、缺外键、缺索引、缺自增默认值、错误枚举均拒绝，保持 revision 012；后置 `models` 表不兼容时也不提前创建指标表。
- 实际 `012 -> 013 -> 014`；新库独立 CLI 可从任意 cwd 初始化，准确 stamp 014，public 21 表、smarteye 8 表，共 29 表。
- `model_services` 全部 ORM 列存在，UUID id 与 JSONB 类型正确；显式 INSERT、提交、另一连接 SELECT，中文 `labels` / `label_mapping` 完整往返，ORM 默认 enabled/timeout/revision 正确。
- 已有 public/其他用户 schema 表、序列、枚举拒绝；已初始化库再次调用拒绝；注入 SmartEye 建表失败后所有表/枚举/stamp 回滚；不提供 URL 的 CLI 拒绝运行。
- 删除前核对 `created=True`、随机名称前缀、非原配置库名、创建时的数据库 OID/owner OID；只删除该精确名称的库；删除后查询确认不存在。没有强制终止其他连接。

## 父代理整合事项

1. 保留线性迁移链 `012 -> 013 -> 014`；013 与 014 均为静态 schema，不导入未来 ORM。业务库只由父代理使用其统一备份和迁移流程，本次未执行。
2. 训练 API 仍有 `project_id: int`、`Optional[int]` 请求/响应/过滤字段，与本次 UUID 修复不符；位于 `backend/backend/api/training_routes.py`，不在本次写范围，需要父代理处理后才能声称训练 API 已兼容 UUID。
3. 本次选择 PostgreSQL INSERT+SELECT 验证模型配置，未重复模型配置 CRUD/TestClient 或真实在线 API 检查；父代理负责 API、服务启动与端到端整合。
4. 已有训练表若不兼容，013 会中止并保留数据；不要自动强转 BigInteger 或删除重建，需单独核对旧 ID 映射与数据后处理。

未运行其他模块或全量测试；父代理正在统一执行。现有 FastAPI/Starlette 生命周期和 Alembic 配置弃用警告不在本次范围。
