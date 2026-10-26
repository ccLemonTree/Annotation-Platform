# Shaoxing Grounding Deployment Implementation Plan

**Goal:** 在绍兴 A100 部署 VLX-Seek 和 GroundingPI，并通过平台模型管理完成实际图片调用。

**Architecture:** 使用已有 Docker 镜像，各占一张空闲 A100，主模型目录只读挂载；复用上游 worker 和 GroundingPI 原生引擎，增加平台现有 multipart 检测协议。GPU 0 和现有容器不变。

**Tech Stack:** Docker、FastAPI、上游 PyTorch/Transformers、现有平台 model-services API。

1. 检查 GPU、镜像、端口、依赖 → 已验证驱动 615.71.09，GPU 1/2 空闲，两个镜像 CUDA 运算成功。
2. 准备依赖 → GroundingPI 已通过镜像原生 Transformers 5.15 的完整加载和实际图片检测，按用户要求保留 5.15；VLX 补充官方 WeDetect 权重，不修改原模型文件。
3. 先写测试，再实现最小 HTTP 适配 → 测试提示透传、归一化坐标恢复、无目标/截断输出、图像与类别校验。
4. 启动持久容器 → 限定 GPU 1/2，记录对应 UUID、设置重启策略、只读权重和独立缓存，分别验证真实图片检测。
5. 平台接入 → 必要的 prompt 透传及未校准分数提示，回归测试，通过管理员 API 新建两条模型配置并真实 predict。
6. 验收 → 记录端点、镜像 ID、GPU、请求示例及限制，清理仅自有临时容器；不提交 Git、不变更已有模型配置。

注意：这两个 VLM 不输出校准置信度，协议占位分数固定 0.5，必须显示警告，不能作为自动通过依据。GroundingPI 使用指定 vLLM 镜像中的原生 Transformers 5.15 和项目处理/解析函数；未启动 vLLM 引擎。归档的 5.7 fork 仅准备源码，未安装或替换运行环境。

部署及平台接口验收已完成，见 `docs/testing/2026-10-20-grounding-models.md`。首次 VLX 请求超时后，仅重启自有容器并补充阶段日志，后续示例图片重复调用和平台调用成功；未把首次超时认定为已定位的驱动或编译故障。
