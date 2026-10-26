# DINOv3 Detection Training Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在平台接入 `dinov3/vits16-ltdetr-coco` 的监督检测微调，生成可独立加载的完整检测权重。

**Architecture:** 复用训练记录、GPU hold、ssh-manager、冻结包校验、日志和产物下载。新增一个 Lightly 训练器，将已有 YOLO/COCO 检测数据适配为 Lightly data 对象；训练容器继续禁网，使用服务器已有完整检测权重。无需数据库迁移。

**Tech Stack:** FastAPI/Pydantic、Python、React/Ant Design、Lightly Train、Docker。

## 已确认的决定与边界

- 用户只有约100张原图，目标是框出电瓶车；采用监督微调，先完成框标注和审核。
- 模型固定为 DINOv3 ViT-S/16 LTDETR，默认镜像按用户指定使用 `lightly/train:latest`。镜像需提前准备，启动记录实际 digest；不自动拉取、下载权重或启动正式训练。
- 支持平台已审核的 train/val 冻结 YOLO/COCO，以及服务器现有数据；保留 test，不将其用于验证。
- 沿用现有管理员操作、租户隔离、GPU占用保护、只读输入、独立输出和手动启动。首版单机，可选择多卡；不新增自监督预训练、自动标注或自动部署。
- 验收区分本地适配测试与真实GPU训练。后者依赖用户数据、镜像和完整权重就绪；不承诺100张图的泛化精度。

## Task 1: 训练契约与数据适配

文件：`backend/backend/schemas/training_run.py`、`services/training_config.py`、`services/training_dataset.py`、`tests/test_dinov3_training.py`。

1. 先写失败测试：目录/YOLO/COCO适配、类别重编号、负样本保留、test隔离、参数拒绝与本地权重限制。
2. 执行 `.venv/Scripts/python.exe -m pytest tests/test_dinov3_training.py -q`，确认因缺失训练器失败。
3. 新增类型化steps/batch/lr/backbone_freeze/precision等参数，沿用目录与冻结校验；脚本调用公开 `train_object_detection` API。
4. 复跑测试，执行生成脚本到隔离替身以核对真实调用参数和输入不变。

## Task 2: 远端预检查与生命周期

文件：`services/training_executor.py`、`services/training_runs.py`、`tests/test_dinov3_training.py`、`tests/test_training_runs.py`。

1. 添加API草稿创建/编辑、格式拒绝、镜像依赖和完整权重CPU加载失败的测试。
2. CPU只读无网络容器校验Lightly API与本地完整权重模型身份；沿用GPU/镜像digest与输出检查。
3. 确认冻结数据使用已有解包目录，产物识别 `.pt/.ckpt`；不改旧训练器。
4. 运行训练、冻结包、产物及Swift相关回归。

## Task 3: 页面、文档与验收

文件：`frontend/src/pages/TrainingPage.tsx`、`scripts/verify_dinov3_training_ui.cjs`、`docs/development/模型训练.md`、`docs/development/CODEX_HANDOFF.md`、`README.md`。

1. 页面显示训练器、完整权重要求、标注前置条件、总batch与步数含义，支持创建/编辑和冻结标签选择。
2. 浏览器隔离API验证创建/编辑、YOLO/COCO、错误、小屏与切换旧训练器；不写业务数据。
3. 运行前端 `npm run build`、`npm run lint`，记录既有问题。
4. 文档列出镜像/权重准备命令、监督训练区别、约100张图的起始参数、输出加载和未完成的真实GPU验收。

接入检查发现 `CheckpointInput`、`checkpoint_predict.py` 和 `CheckpointFields.tsx` 也限定旧检测类型，需同步接入图片效果预览/模型验证；沿用已有权重哈希、容器隔离、停止与GPU保护，类别直接从Lightly完整权重读取。不新增部署服务。
