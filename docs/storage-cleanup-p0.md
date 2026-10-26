# P0 无引用原图安全核查

本次交付管理员只读扫描、在线安全拒绝入口，以及离线可恢复隔离归档CLI。现有上传先落盘、随后注册并提交；SmartEye 使用独立同步 runtime 连接。在线入口没有贯穿文件写入至引用提交的共同锁，因此始终拒绝执行。实际归档与恢复只允许在停止所有API/worker/SmartEye写入后的离线维护中操作；不能声称在线并发安全。

## 父代理集成

后端实际应用根为 `backend/backend`。在其 `api/__init__.py` 中由父代理统一加入：

```python
from api.storage_cleanup_routes import router as storage_cleanup_router
api_router.include_router(storage_cleanup_router, prefix='/storage-maintenance', tags=['存储维护'])
```

外层应用已有 `/api` 前缀，最终端点如下，均复用 `get_current_superuser` 与 JWT 认证，仅允许 `role=admin`：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/storage-maintenance/preview?limit=200` | 只读核查，limit 为 1–1000，仅截断展示列表，汇总覆盖完整核查范围 |
| POST | `/api/storage-maintenance/execute` | 重新查询引用并检查所选路径，返回 409 安全拒绝；从不执行文件变更 |

执行请求示例（SHA256 与目录前缀必须一致）：

```json
{
  "paths": ["images/aa/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"],
  "confirmation": "QUARANTINE"
}
```

请求最多100个路径，拒绝绝对路径、反斜杠、跳转路径、非哈希命名和额外字段。缺少明确确认返回422。确认文本不是并发安全凭证；当前不存在启用执行的配置开关或令牌。

409 返回 `detail.code=concurrency_guard_unavailable`、`execution_available=false`，以及 `revalidated` 当前检查结果；即使所有 `reasons=[]` 也仍然拒绝。引用核查失败返回503，不返回“安全候选”。非本地存储、根目录缺失或链接/备份路径返回409。

前端独立组件 `frontend/src/pages/StorageMaintenancePage.tsx` 已提供扫描、保护计数、候选表与不可执行原因。由父代理统一注册路由与管理员菜单，例如在现有布局内加入：

```tsx
import StorageMaintenancePage from './pages/StorageMaintenancePage';
// 加入现有 Routes；沿用项目既有管理员菜单控制。
<Route path="storage-maintenance" element={<StorageMaintenancePage />} />
```

页面不自动扫描；用户点击“扫描预览”触发请求。失败时清空旧预览，归档按钮保持禁用。本功能无新增表，无迁移。

## 核查边界

- 仅使用已配置的本地存储实例，拒绝实例与配置不一致；不接受用户指定存储根。
- 只枚举 `images/<SHA256前两位>/<SHA256>.jpg|png|bmp|webp` 两层目录。不递归进入 `.backups`、`.incoming`、`exports`、`smarteye` 或任意其他目录。
- 跳过链接/目录联接，保护 `st_nlink != 1` 的硬链接文件，不把共享磁盘块计为可释放空间。
- 查询所有租户的 `LibraryImage`、`Asset` 路径与哈希；不按当前管理员所属租户过滤。
- 查询冻结导出 `ExportSnapshot` 路径、哈希及 manifest 中的原图哈希。导出ZIP始终在扫描范围之外。
- 直接查询 `integrations.smarteye.runtime` 的真实 `smarteye_files(blob_path, content_hash, relative_path)` 与 `smarteye_jobs(status)`；包括下载成功但未入图片库的记录。runtime 必须与主存储使用相同根目录。实际表缺失、字段不匹配或不可访问即停止核查；不靠 ORM 历史表代替 runtime。
- 若主库实际存在旧 `smarteye_downloads`，同时保护其路径与哈希。
- 核查 `image_uploads`；任意租户存在未完成上传时，保守保护所有候选。runtime 存在非 completed/failed/cancelled/interrupted 任务时也保护全部候选。暂存分片、冻结ZIP与下载链接不进入扫描。
- 文件最后修改时间至少七天才列为候选。mtime 是筛选宽限，不能排除“已落盘、尚未提交引用”的窗口。扫描是多个数据源的即时观察，不是事务一致性证明。

## 离线归档与恢复

先停止并核实所有API实例、上传/分片入库worker、SmartEye下载/重试/定时调度进程、分类/训练worker及任何外部存储写入工具。仅暂停网页任务或查询一次连接数不够。确保服务管理器不会自动拉起这些进程，客户端不能继续上传。离线前通过现有业务流程处理未结束任务，CLI本身不会取消任务、杀连接或修改引用记录。

CLI必须显式给出目标PostgreSQL URL与绝对storage路径，不会从`.env`推断这两个操作目标。配置模块仍按项目现有方式加载，因此JWT等应用配置须有效。URL指定的数据库必须同时包含主业务引用表与真实SmartEye runtime表，不能指向单独的历史下载库。目标不能是postgres/template系统库。

命令只由操作员在完成上述停写后执行，以下是操作说明，本轮没有对业务库运行：

```powershell
Set-Location 'F:/hzxf/数据管理平台/backend/backend'
# 此变量由操作员显式设置为目标库URL；不要把密码写入清单或提交到代码库。
.venv/Scripts/python.exe -m services.storage_cleanup archive `
  --database-url $env:STORAGE_CLEANUP_DATABASE_URL `
  --storage 'F:/explicit/offline-storage' --confirm-writers-stopped
```

运行时需超级用户权限，并能连接同一服务器的postgres控制库。CLI先取得storage内的操作系统排他维护锁，再检查目标库除自身外没有连接（包括idle连接）。由同集群postgres控制连接设置目标库`ALLOW_CONNECTIONS false`，再次核查现存连接与预备事务；这堵住检查后新DB连接进入的窗口，不证明外部文件写入进程已停止。任何其他连接、预备事务、未完成上传、未结束SmartEye任务、待执行/执行中分类、旧SmartEye任务或训练任务都会拒绝。

连接入口关闭后，CLI复用全部跨租户路径/哈希/冻结manifest/runtime引用查询，重新枚举已知images目录，不消费旧扫描结果。逐文件核查七天mtime宽限、路径/联接/硬链接、实际SHA256与文件大小；候选内容必须与哈希文件名一致。每次移动前再次核查资格、内容和单链接条件。

归档目标固定为同一storage内`.quarantine/<run_id>/images/...`，仅使用同文件系统rename；不会unlink原图、备份或下载链接，不做跨盘复制后删除。隔离不会释放磁盘块，也不自动清空隔离区。存在目标文件时拒绝覆盖。

首次移动前，先独占创建并flush/fsync不可变`manifest.json`，记录run_id、storage根、数据库名/OID/集群标识、时间及每个原相对路径/大小/SHA256；不记录数据库URL或密码。POSIX额外同步目录元数据，Windows使用文件fsync及文件系统自身的rename/journaling，未承诺任意硬件断电情况下的跨平台事务性。发生中途错误，清单和已隔离文件保留，数据库连接入口在finally中恢复；CLI退出2，操作员必须保持全部服务停机并先核对清单。

恢复命令同样要求停写、显式目标与连接/任务检查，run-id取归档返回值或隔离目录名：

```powershell
.venv/Scripts/python.exe -m services.storage_cleanup restore `
  --database-url $env:STORAGE_CLEANUP_DATABASE_URL `
  --storage 'F:/explicit/offline-storage' --confirm-writers-stopped `
  --run-id '<32位归档run_id>'
```

恢复校验清单绑定的storage和数据库身份、每条已知哈希路径、实际内容和硬链接数。隔离文件存在且原位置为空才rename回原位置；原位置已存在时拒绝覆盖，保持两份文件。清单计划的文件若未发生移动或已恢复，则核对原位置内容后跳过，支持重复恢复以及中断归档后的部分恢复。清单与空隔离目录均保留，不自动删除。恢复完成、核对业务引用及下载正常后才由操作员重启服务。

若进程被强杀、控制连接断开或机器断电，finally可能无法恢复数据库入口。保持所有写入进程停止，从同一服务器的postgres库由DBA执行（替换并正确引用目标库标识符），再按清单恢复：

```sql
ALTER DATABASE "explicit_target_database" ALLOW_CONNECTIONS true;
```

这是一项可见的离线数据库维护操作；CLI不会强制终止其他会话，也不会因无法证明停写而降级执行。未来若需要在线隔离，必须先让全部写入端共同遵守贯穿落盘到引用提交的跨进程协议；本次没有启用在线写入开关。

## 隔离测试

在 PowerShell 7 中运行，临时目录不得使用另一并行任务的 pytest basetemp：

```powershell
Set-Location 'F:/hzxf/数据管理平台/backend/backend'
.venv/Scripts/python.exe -m pytest tests/test_storage_cleanup.py tests/test_storage.py -q --basetemp=.pytest-tmp-cleanup
$env:P0_STORAGE_CLEANUP_POSTGRES='1'
.venv/Scripts/python.exe -m pytest tests/test_storage_cleanup_offline.py -q --basetemp=.pytest-tmp-cleanup
Set-Location 'F:/hzxf/数据管理平台/frontend'
npm run build
```

在线测试通过独立FastAPI应用注册本路由，复用测试库认证依赖。离线测试只在postgres控制库创建并最终删除自己命名的`codex_cleanup_<随机UUID>`空库，使用tmp_path文件；对目标库禁连、归档、故障注入及恢复均限于这些隔离资源。不会连接、扫描或变更业务库/业务原图。测试覆盖跨租户引用、runtime与冻结包、上传/下载保护、权限、路径、硬链接/目录链接、引用表失败、重新核查、在线拒绝、离线连接封锁、未结束任务拒绝、中断后恢复及拒绝覆盖。没有修改共享conftest、pytest.ini、模型或路由注册文件。
