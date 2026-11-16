# 嘉善移动 K8s 多机训练实施计划

**Goal:** 在现有训练页接入嘉善集群，支持 YOLO、RF-DETR Medium/2XLarge 与 WeMM 的多节点、多 GPU 训练。

**Architecture:** 保留现有 Docker 默认流程，在训练 JSON 配置增加 execution_backend 与 k8s 拓扑。通过现有 ssh-manager 桥接在嘉善控制机提交独立 ConfigMap、主节点 Service 和每节点 Job；复用 aisfprod/nfs-pvc-ai，输入只读、按任务 UUID 隔离输出。

**Tech Stack:** FastAPI/Pydantic、现有训练后台队列、kubectl batch/v1、PyTorch torchrun、Swift、React/Ant Design。

## 约定与边界

- 依据 `/opt/yolo-k8s/ddp-train.yaml` 与 `/opt/k8s/wemm-text2image-aligner-lora-token768-5node.yaml`，支持多机同构 GPU 数，页面选择节点与每节点卡数。
- NFS 为 `172.22.132.169:/adb-ssd-mount/ai`；检测数据与输出限定 `chen/training`，WeMM 数据还允许用户提供的既有 `chen/train/wemm/dataset`，基础权重可复用同 PV 中已有模型。
- WeMM 复用原基础模型、保留 token768/LoRA 配置，从头微调，不自动续训原 checkpoint。
- 只允许管理员提交/停止；保留租户隔离、任务 UUID 所有权校验、取消标记和不确定状态。
- 不启动真实训练；验收使用隔离测试、页面测试、集群只读预检与服务端 dry-run。

## 实施与验证

1. 新增 K8s 配置验证与生命周期路由，复用 TrainingRun JSON，无业务数据库迁移。先写配置/调度/隔离测试，再接入远端桥接；验证旧 Docker 训练回归。
2. 新增标准库 K8s 执行器，在控制机独立安装并使用 Python3。验证模板、污点、资源请求、幂等/取消、部分失败、日志和产物路径；远程仅执行必要镜像检查与 dry-run，不启动真实训练。
3. 按真实模板生成四类训练脚本与 WeMM 配置。验证节点数/进程数、分布式启动语义、原模型路径、许可证和输出隔离。
4. 增加页面环境切换、节点/GPU配置、详情与日志选择；验证保存/编辑/克隆与构建，不影响原训练和导出任务。
5. 更新使用说明，本地相关测试和构建通过后同步本次文件至115；重启前核对活动训练、验证、上传及下发，验收服务健康及管理员接口。

## 补充：K8s 平台数据来源

用户确认增加平台标注/冻结数据来源。复用既有租户、格式与标签校验及冻结包 SHA256 缓存；SFTP 上传至控制机实际 NFS 挂载，控制机单次解包、校验并准备检测标签，所有 rank 只读使用就绪数据。验证四训练器的缓存命中/未命中启动顺序、实际115至嘉善传输、Linux硬链接与标签筛选、只读清单和页面保存/编辑/复制；不启动真实GPU训练。
