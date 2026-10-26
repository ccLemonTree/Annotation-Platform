# 训练模型导出与 LoRA 合并

目标：从训练任务详情创建独立的 ONNX / TensorRT 导出或 Swift LoRA 合并任务，指定 batch、执行服务器/GPU，保留日志和可下载产物。

复用 TrainingRun 生命周期、GPU 占用、SSH/MCP、独立 Docker 目录、停止/核对与产物下载，不增加数据库表。子任务使用 model_export / model_merge 类型，source 保存原任务及模型类型；已有训练配方不变。配置保存不执行；启动前验证镜像和来源，容器无网络，输入只读。服务器切换时要求管理员明确填写目标服务器上的来源路径，不隐式传输大权重。

1. 隔离测试先覆盖 batch 范围/格式/来源/权限、动态 ONNX 与 trtexec 参数、Swift argv、产物完整性；验证旧接口不存在或测试失败。
2. 添加请求模型、子任务创建接口、输出脚本与远端检查，接入既有开始/停止/日志/产物流程。RF-DETR 从 checkpoint 识别模型和分辨率；trust_checkpoint 仅显式勾选。YOLO 默认 opset13、simplify、input/output 命名；动态 batch 保持固定空间尺寸。TensorRT 使用指定导出镜像及实际 ONNX 输入名称。
3. 添加训练详情“导出 / 合并”配置入口，单独任务展示执行类型、来源、输出位置、batch/精度和镜像；LoRA 默认原训练镜像，仅 LoRA 可合并。保留基础模型、adapter 原文件。
4. 验证：聚焦后端与原训练生命周期回归、前端构建、浏览器隔离数据走通创建/错误/参数与子任务入口；真实平台只读核对可用镜像和活跃任务。远端实导出需就绪镜像与指定权重，未实测必须明确报告。

假设：使用者为现有管理员；一次任务绑定一张空闲物理 GPU；动态 batch 采用 min≤opt≤max，固定 batch 不传 TRT profile；同一子任务不覆盖既有文件。只支持请求的 YOLO、RF-DETR、Swift/WeMM LoRA，不扩展 DINOv3 或量化。
