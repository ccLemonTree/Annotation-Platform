# 主后端

当前应用为FastAPI，入口是[main.py](main.py)，主React前端位于项目根目录的`frontend/`。本目录包含模型、服务、schemas、Alembic迁移和测试。

2026-10-15当前版本为014：013补齐训练三表及UUID项目引用，014新增独立模型服务配置。`/api/model-services`与模型管理页面、数据集来源/划分预览及管理员存储核查已接入。后端全回归284项通过，首个管理员创建另补2项测试；真实模型请求和具体限制见[整合验收](../../docs/testing/foundation-datasets-models-verification-2026-10-15.json)。后面的012、189项等是此前时点记录。

2026-10-15新增`/api/image-uploads`分片接口和单进程后台导入器，图片/文件夹/ZIP/TAR沿用图片集规则、原图去重与共享场景。**运行前需升级到012**；新增两张上传表，旧业务行和配置保留。189项完整回归通过；配置/恢复期限/接口见[上传说明](../../docs/development/文件夹与压缩包上传.md)。

追加：场景分类改为默认4路有界并发，环境变量`SCENE_CLASSIFICATION_CONCURRENCY`范围1–16，全后端共享。195项完整回归与真实队列短测通过；两个现有图片集经备份合并，合并维护函数要求排空上传/分类并由调用方备份、提交事务，未增加公开合并API。详情见[本轮证据](../../docs/testing/collection-merge-scenes-verification-2026-10-15.json)。

## 本地开发

使用PowerShell 7和本目录的项目虚拟环境。当前已建立`.venv`，运行依赖在[requirements.txt](requirements.txt)，测试依赖在[requirements-dev.txt](requirements-dev.txt)。首次建立环境时执行：

```powershell
Set-Location 'F:\hzxf\数据管理平台\backend\backend'
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

配置本目录的`.env`，字段参照[.env.example](.env.example)。配置文件只供本机使用，不提交；`JWT_SECRET_KEY`必须至少32字符。数据库使用`DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`，不要只设置旧`DATABASE_URL`字段。`DEFAULT_TENANT_ID`应指向已存在的单租户：本机现库为`postgres`，现有管理员所属租户为2；示例的1仅适用于对应的新库。不要直接覆盖已有`.env`。

启动主后端：

```powershell
pwsh -NoProfile -File 'F:\hzxf\数据管理平台\backend\start_server.ps1'
```

脚本固定进入主后端目录并使用`.venv\Scripts\python.exe`。8000端口已有服务时先核对进程，避免启动多个实例。入口：[健康检查](http://127.0.0.1:8000/health)、[Swagger](http://127.0.0.1:8000/docs)。健康响应只能证明进程能响应。

## 验证与数据库

从本目录运行隔离测试：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

默认跳过真实PG及加载器用例。完整验证同时设置P0/P1/P2/P3_POSTGRES_INTEGRATION='1'、P2_DATASET_POSTGRES_INTEGRATION='1'、P0_STORAGE_CLEANUP_POSTGRES='1'，及`$env:P2_LOADER_PYTHON=(Resolve-Path '.backups/loader-env/Scripts/python.exe').Path`；并行运行必须指定独占`--basetemp`。PG需要创建临时库权限，离线清理测试需要对其自建临时库进行连接封锁的权限，不操作业务库。SDK依赖与复现见[导出说明](../../docs/api/检测标注与导出.md)，不安装进运行API环境。

从项目根目录运行`.\backend\backend\.venv\Scripts\python.exe verify_setup.py`只读检查入口、配置、表与迁移版本。当前014及所需表存在检查通过；脚本不自动建表，不代表业务/模型效果验收。

本机005/006/007/008/009/010/011经备份与独立库测试后应用，保留原数据/主键；005增加内容哈希约束，006内置SmartEye持久表/schema，007增加版本唯一索引与事件/冻结导出表；008回填独立images图片目录并关联assets，保留现有标注ID与文件；009增加命名图片集及成员关系，旧图保守加入历史图片；010新增入库设置和场景标签/状态，不移动原图；011将现行规则复制为各图片集独立配置，增加待分类规则快照，场景结果继续跟随原图共享。发现重复版本时拒绝迁移，不丢弃历史。003/004含删表流程，不重跑旧迁移或执行`init_db/drop_db`。私有备份位于已忽略的`.backups/`，不公开；全新库初始化另验。

全新空库使用[scripts/bootstrap_database.py](scripts/bootstrap_database.py)，显式提供DATABASE_BOOTSTRAP_URL，可带`--admin-username`/`--admin-email`及BOOTSTRAP_ADMIN_PASSWORD环境变量在同一事务创建首个管理员；非空库拒绝，不重跑旧001—004。见[初始化验证](../../docs/testing/p0-database-closeout-2026-10-15.md)。Compose镜像实际构建/运行仍未验收。

## 当前接口与限制

- 独立图片库入口为`/api/images`，无需项目上传；assets是项目使用记录。同一原图可用于多个项目，标注与审核独立，删除项目保留目录图片。见[图片库说明](../../docs/development/图片库与项目使用说明.md)。
- 上传/SmartEye最终图片按目标图片集独立长边阈值（任一边大于等于阈值，初始720）；合格图片入库后由持久后台队列调用可配置的Qwen图文服务做场景多标签分类。同集内容去重；场景跟原图共享，手动重试需指定图片集规则，结果更新所有所属集。失败保留并支持重试，场景不改标注类别。单后端进程启动/停止分类工作器，重启处理中的分类标记中断失败。设置和接口见[场景与入库操作](../../docs/development/图片场景与入库规则.md)。
- 项目/资产/标注详情主键均为`id`，项目ID为UUID；上传响应使用`asset_id`。
- 图片通过带Bearer Token的`GET /api/assets/{id}/content`读取，前端使用认证请求生成Blob URL。
- 批量状态/审核接口请求体为ID数组，状态和拒绝原因放查询参数。失败响应使用`success/code/message`；HTTP异常保留兼容字段`detail`和认证头，422不回显输入值。
- SmartEye原Flask业务模块已内置到本进程，统一认证前缀`/api/smarteye`；运行前必须完成012。无需启动原SmartEye服务。私有源平台配置使用`.env.smarteye`，与主数据库配置隔离。
- 当前限单进程、本地同卷硬链接；图片下载可直接关联项目，取消/旧轮次拒绝迟到写入，重启不自动继续付费AI或调度。真实上游效果、原历史数据迁移及EOS/Docker待验收，见[内置说明](integrations/smarteye/README.md)。
- 检测更新需base_version，生成新版本而不改旧内容；审核和计数只按最新版本。YOLO/COCO默认ZIP含图片/框/manifest，可冻结明确划分；旧COCO JSON调用追加package=false，见[标注与导出接口](../../docs/api/检测标注与导出.md)。
- 删除资产移除引用，暂保留物理图片；通用YOLO/COCO加载器已验收，用户训练脚本、实际训练和任意裁剪泄漏效果仍待验收。无引用文件/ZIP清理、大规模导出队列和近邻索引尚未实现。

最新状态见[当前TODO](../../docs/development/TODO.md)及[P2证据](../../docs/testing/p2-verification-2026-10-13.json)，历史报告只作为参考。

[项目入口](../../README.md) · [文档目录](../../docs/README.md)
