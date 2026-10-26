# 数据管理平台

2026-10-26：开发与运行已迁移到115服务器，访问 http://192.168.1.115:3000 。主目录`/ai/chen/code/workspace/数据管理平台`，后续修改、调试、测试及构建均在服务器执行；Windows副本仅作回退。详见[115服务器开发与运行](docs/development/115服务器开发与运行.md)。

2026-10-19新增DINOv3 ViT-S/16 · LTDETR监督检测微调：使用 `dinov3/vits16-ltdetr-coco` 完整权重和 `lightly/train:latest`，支持平台YOLO/COCO冻结标注、任务日志/产物下载、权重效果预览与模型验证。原始图片需先完成框标注；镜像/完整权重需在训练服务器准备。真实GPU训练与效果待验收，见[模型训练说明](docs/development/模型训练.md#dinov3-vit-s16--ltdetr-检测微调2026-10-19)。

主项目包含数据拉取、数据管理、数据标注三个模块。SmartEye按完整内置的方向开发，图片统一存储；第一版采用单租户。

2026-10-16新增管理员[SSH调试](http://localhost:3000/gpu-terminal)：复用GPU服务器连接，通过ssh-manager MCP打开完整交互终端，支持top、vim、Ctrl+C与窗口缩放。绍兴A100真实页面已验证；操作、部署和会话边界见[SSH调试说明](docs/development/GPU服务器SSH调试.md)。

2026-10-16新增[模型训练](http://localhost:3000/training)：YOLO26、RF-DETR Medium/2XLarge、Swift大模型SFT和WeMM向量微调，全部示例参数可配置；支持检测冻结导出集或服务器已有路径，管理员预检查后明确启动，查看状态、日志和产物。018只增训练记录与GPU占用表，不自动启动正式训练。操作与限制见[模型训练](docs/development/模型训练.md)。

2026-10-16新增：[验证集](http://localhost:3000/validation)。从人工审核标注或冻结val/test保存真值，选择模型管理中的检测服务，查看P/R/F1、mAP、分类别指标、混淆矩阵和逐图叠框；支持取消、重试及JSON报告。数据库增量016，原图和现有标注保留，操作与限制见[验证集说明](docs/development/模型验证集.md)。

本轮新增：[预标注与AI审核](http://localhost:3000/automation)、[图片描述与检索](http://localhost:3000/image-semantics)、[质量与效率分析](http://localhost:3000/analytics)。支持配置置信度和框外扩，正样本满足联合规则直接通过，负样本全部管理员最终审核；图片库/项目预览支持左右键跨页。数据库增量升级015，原图和已有标注保留。见[操作说明](docs/development/智能处理与审核.md)。

2026-10-16 GPU资源页面：管理员打开 [GPU资源](http://localhost:3000/gpu-resources)，填写IP、端口、账号和密码添加NVIDIA服务器，测试连接并自动获取显存、利用率、温度、功耗及计算进程。密码加密保存不回显，原绍兴A100连接保留；支持多服务器切换、修改及删除连接。数据库增量017，远端只读监控，未包含训练调度；配置和边界见[操作说明](docs/development/GPU资源管理.md)。

2026-10-15当前新增：侧栏[模型管理](http://localhost:3000/models)支持配置、启停与图片试运行，工作台可预测预览并应用到可撤销草稿；数据集导出增加来源分组、划分建议和可配置校验；[存储维护](http://localhost:3000/storage-maintenance)提供管理员预览，实际隔离归档通过停写后的离线工具完成。数据库已增量升级014，原图与标注保留。入口与边界见[模型操作](docs/development/模型服务管理.md)、[数据集说明](docs/testing/p2-04-dataset-splits.md)、[存储操作](docs/storage-cleanup-p0.md)及[当前验收](docs/testing/foundation-datasets-models-verification-2026-10-15.json)。下方测试数量与“未接模型”等描述保留历史时点，当前状态以上述记录和TODO为准。

2026-10-15追加修复：上传默认当前/上次有效/已有第一图片集，不再默认当天日期；“1015”已合并至“绍兴嘉兴误报-1015”，共4676张，原图、分类及上传回执保留。场景分类默认4并发，20秒短测16→39张，195项完整回归通过。刷新页面后查看统一图片集，见[验收](docs/testing/collection-merge-scenes-verification-2026-10-15.json)。

2026-10-15新增文件夹、ZIP/TAR上传与8MiB分片续传：图片管理上传窗口支持暂停/继续，刷新后重选同文件只补缺片，沿用图片集规则、去重和共享场景。数据库已升级012，升级时现有1项目、1图片集、3账号及平台/模型配置保留，189项完整回归通过。见[上传说明](docs/development/文件夹与压缩包上传.md)。

2026-10-15此前已按用户要求清空全部历史业务数据；账号、模型默认设置和SmartEye平台配置保留。此前test-img及各样本数量为历史验收记录，之后用户新建的数据继续保留。私有备份位置与验证见[历史清理记录](docs/testing/history-reset-2026-10-15.json)。

当前采用独立图片库：上传／SmartEye拉取先登记图片，再筛选添加到标注项目。同一原图可用于多个项目，标注与审核独立，项目只创建使用记录。图片集可命名为1013/1014，SmartEye按平台名归类，已有图片可多选加入；图片管理固定为独立图片库；项目管理“管理图片”进入显示项目名的项目图片页，选图在当前页抽屉完成，标注后返回同一项目。

P0/P1及P2人工检测、版本／审核、图片包和冻结流程已实施；008已备份回填6张现有图片，19项目、6标注版本与2冻结包保留。test-img类别person，后续标注以平台最新记录为准。10月14日176项完整回归通过；导航简化的验证见[导航验收](docs/testing/project-navigation-verification-2026-10-14.json)。模型预标注、EOS、用户训练脚本和真实子平台效果未验收。详见[当前TODO](docs/development/TODO.md)、[图片库说明](docs/development/图片库与项目使用说明.md)、[检测标注与导出](docs/api/检测标注与导出.md)和[SmartEye内置说明](backend/backend/integrations/smarteye/README.md)。

新增场景分类与入库规则：每个图片集独立配置分辨率、分类开关、场景及vLLM模型地址/名称；初始长边≥720，本次可覆盖。同集内容去重，同图跨集共享原图和场景；手动重分类更新共享结果。Qwen3.8-27B已真实接入，图片先入库再后台分类，失败保留且可重试；日期/平台图片集与检测类别独立。见[操作说明](docs/development/图片场景与入库规则.md)和[图片集配置验收](docs/testing/collection-ingest-verification-2026-10-14.json)。

标注与审核现在先按标注项目展示进度，再进入项目内图片列表；审核在现有画框工作台看图完成，切图返回保留原项目和筛选。详见[标注与审核验收](docs/testing/project-review-verification-2026-10-14.json)。

## 从哪里开始

| 要做的事 | 入口 |
|---|---|
| 了解当前状态、确认的需求和下一步 | [Codex接手记录](docs/development/CODEX_HANDOFF.md) |
| 按验收顺序开发、查看原TODO逐项状态 | [当前开发TODO](docs/development/TODO.md) |
| 查找需求、设计、接口和测试文档 | [文档目录](docs/README.md) |
| 开发主前端 | [前端说明](frontend/README.md) |
| 开发主后端 | [后端说明](backend/backend/README.md) |
| 查阅旧版本和历史开发报告 | [历史归档](docs/archive/README.md) |

## 目录

```text
数据管理平台/
├── README.md             # 项目入口
├── frontend/             # 主前端：React + TypeScript + Ant Design
├── backend/
│   ├── backend/          # 当前FastAPI应用、数据库迁移和测试
│   └── ...               # 早期代码及脚手架，保留待后续核对
├── docs/
│   ├── README.md         # 文档导航及使用顺序
│   ├── requirements/     # 长期需求
│   ├── design/           # 实现方案、架构与数据库设计
│   ├── api/              # API规范草案
│   ├── testing/          # 测试计划
│   ├── development/      # 当前开发状态与接手记录
│   └── archive/          # 历史文档，按归档日期和原路径保存
├── data/                 # 业务数据
└── verify_setup.py       # 主后端环境检查，报告缺失表，不自动建表
```

代码入口分别是[主前端App.tsx](frontend/src/App.tsx)和[主后端main.py](backend/backend/main.py)。端口配置为前端3000、后端8000。运行前应准备依赖和后端本地配置；启动步骤见各模块说明。

## 文档维护

- 已确认的第一版范围以接手记录中的决定为准，需求文档保留长期规划。
- 新的需求、设计和测试说明放在对应的`docs/`目录；重复的进度、交付和完成报告进入归档。
- 修改功能时同步更新相关文档和接手记录；验收结论注明具体检查和结果。
- 文档整理保留了现有代码、配置、许可证、依赖文件与数据。后端多层嵌套和旧Vue脚手架留待后续代码整理。

## GitHub与代码同步

仓库：https://github.com/ccLemonTree/Annotation-Platform ，主分支`main`。以115服务器工作目录为准，在服务器提交和同步；Windows仅保留旧代码副本。数据、模型、`.env`、SSH配置及测试产物不入库。

手动初始化脚本通过`ANNOTATION_ADMIN_PASSWORD`和`ANNOTATION_TEST_USER_PASSWORD`读取初始密码；手动登录测试使用`ANNOTATION_PASSWORD`，爬取示例使用`SMARTEYE_ACCESS_TOKEN`。请在私有环境中设置，不要写入代码或日志。既有账号密码不因此改变。
