# VLX-Seek / GroundingPI Deployment Implementation Plan

**Goal:** 先部署VLX-Seek，再在GroundingPI镜像就绪后部署GroundingPI；在平台模型管理登记并通过真实图片调用。

**已于2026-10-20被新部署方案替代：用户明确改为绍兴 A100，两服务已部署并接入平台。** 以 `docs/plans/2026-10-20-shaoxing-grounding-deployment.md` 及 `docs/testing/2026-10-20-grounding-models.md` 为准，不恢复本文的嘉善部署或驱动改造流程。

**当前状态：用户于2026-10-19明确暂停部署/运行库改造，准备自行评估升级580驱动。** 自有`vlx-seek-probe`已删除，162节点查询无GPU计算进程；未安装兼容PyTorch运行库、未创建正式Deployment/Service、未登记平台模型。Qwen两副本仍1/1且零重启。恢复时先重新核对环境，不继续此前CUDA12.4运行库改造计划。

新发现：节点CentOS7、内核`3.10.0-957.el7.x86_64`；NVIDIA 580.105.08官方最低Linux内核4.15，不能按当前内核直接安装580。580配合CUDA13.4仍需前向兼容库；驱动升级也不解决T4单卡显存、BF16/标准FlashAttention2限制。官方依据：`https://download.nvidia.com/XFree86/Linux-x86_64/580.105.08/README/minimumrequirements.html`、`https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html`。

**Architecture:** 嘉善移动`10.11.12.123`跳板机管理的`aisfprod`命名空间，新建独立Deployment/Service/ConfigMap，通过现有nfs-pvc-ai（172.22.132.169:/adb-ssd-mount/ai）只读使用用户模型。使用平台现有model_service检测协议，原有Qwen/向量/检测服务不变。

**Tech Stack:** Kubernetes 1.16、指定Docker镜像、PyTorch/Transformers、FastAPI、现有平台模型API。

## 已核实的边界

- VLX镜像已缓存于162/164/168等节点；空闲节点162/164/168为7张16GB T4，驱动550.76。既有Qwen位于163/165，向量/检测位于155/159/160，不调整其副本或GPU。
- 主权重位于NFS的`chen/code/huggingface/VLX-Seek-1.5-10B`，单文件20,033,961,788字节；不改原模型文件。
- 指定VLX镜像包含Torch 2.14/CUDA13.4及Transformers5.13；原运行库GPU初始化失败，内置615兼容库返回803。不升级主机驱动。
- 在`chen/deploy/vlx-seek`独立目录准备兼容运行依赖、WeDetect权重和服务缓存；模型目录只读。
- GroundingPI按用户要求后部署，先不创建无效模型记录或启动未就绪镜像。

## 执行与验证

1. GPU/镜像探针：新建自有probe Pod，限定162节点及2张调度GPU；验证CUDA、依赖、模型文件、WeDetect和分片兼容性。探针失败不重启线上服务。
2. 兼容运行库：在指定镜像基础上用独立目录准备CUDA12.4 PyTorch，FP16/SDPA及参考线性注意力，验证张量计算和完整模型加载后再创建正式服务。
3. 最小检测HTTP适配：复用原worker、候选框检测器，提供/health、/openapi.json和multipart检测接口；校验输入、串行推理、原图像素框。VLM无校准置信度时明确标注，不编造高置信度导致自动审核。
4. Kubernetes：持久ConfigMap/Deployment/NodePort，Recreate避免重复占用，模型只读、缓存独立，记录实际imageID及GPU分配；健康检查仅在模型真正加载后通过。
5. 平台：实际图片请求成功后通过管理员模型API创建独立KEY，再测试连接和预测；不覆盖既有模型配置或保存伪造标注。
6. GroundingPI：检查镜像拉取状态与官方vLLM/Transformers适配，按相同部署、真实请求、平台注册顺序完成；镜像未就绪时明确保留待办。
7. 文档与清理：记录端点、请求例、兼容参数及实际验证边界，删除仅自有探针，保留正式服务与恢复文件。
