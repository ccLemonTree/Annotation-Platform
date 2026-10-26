# 训练模型导出与 LoRA 合并验证

## 使用入口与行为

训练任务详情 → 导出模型 / 合并LoRA模型，也可在检测权重产物上选择“导出此权重”。保存后生成独立待启动任务，先预检查再启动；复用现有GPU占用、日志、停止、核对状态、产物下载。

- YOLO：ONNX与TensorRT；默认640、opset13、simplify、input/output命名。导出使用检查点副本，ONNX维持FP32，TensorRT可FP16。
- RF-DETR Medium / 2XLarge：`RFDETR.from_checkpoint`自动识别模型与分辨率；可覆盖正方形分辨率，显式选择trust_checkpoint，支持FP16 ONNX。
- 固定batch可配置；动态batch使用min/opt/max，ONNX按opt追踪且保持batch维动态。TensorRT按实际ONNX输入名构建profile；固定图不能伪装成动态图。
- TensorRT默认镜像为用户指定的 `aisfh5.com:9091/triton-server/triton-server:25.11_py3_vllm`；使用 `/usr/src/tensorrt/bin/trtexec`，生成engine并保留ONNX。
- Swift SFT / WeMM：仅LoRA支持合并；使用原训练镜像、基础模型、adapter、可配置model_type和torch_dtype。全参数训练无需合并。
- 一任务绑定一张物理GPU。切换执行服务器后须填写目标服务器已有来源路径，不自动传输大权重。容器输入只读、禁网、不自动安装依赖或拉取镜像，输出UUID独立目录。
- `result.json`记录来源标识、产物SHA256、参数、GPU名称/UUID、依赖版本。ONNX原图及外部张量、adapter均检查变化；基础模型记录config哈希和权重文件大小/时间，未对大型基础模型逐分片计算SHA256。

## 验证证据

- `backend/backend/tests/test_model_output.py`与训练/产物/DINOv3/Swift/数据集回归：150 passed、1 skipped（Windows符号链接权限）；日志 `output/model-output-regression.log`。
- 随后新增ONNX外部张量来源标识测试：1 passed；日志 `output/model-output-external.log`。
- `scripts/verify_model_output_ui.cjs`：TensorRT 1/7/8 profile与GPU/信任选项、服务端错误后保留表单、待启动子任务、WeMM沿用训练镜像与基础模型、YOLO batch4及input/output/opset13、移动端提交。4次请求由隔离API接收，真实业务写入0。
- 前端针对修改文件oxlint通过，`npm run build`通过，仅原有的大包体积警告；日志 `output/model-output-build.log`。
- 真实ONNX图检查器验证重命名和batch形状；模型库调用、Swift/trtexec命令、容器侧成功/失败清单及源变动通过隔离数据验证。未实际执行大型模型导出、engine构建或LoRA合并。

## 上线状态与阻塞

2026-10-22 检查了本地活跃工作：远端训练任务仍运行，验证、checkpoint执行、项目标注处理、语义处理、上传、场景分类无活跃任务。尝试停止并重启本地8000后端以加载新路由，被执行工具自动审批以blocked by policy拒绝，未给出更具体理由；旧后端仍在运行，新 `/api/training/runs/{id}/outputs` 尚未加载。需要由操作者重启后端；无需数据库迁移。未用其他方式绕过该拒绝。

用户授权在绍兴A100拉取导出镜像。拉取过程中发现根分区100%，`/var/lib/containerd`约177GB；Docker data-root为 `/ai/docker`，`/ai`约664GB可用。已终止本次确认为该镜像的docker pull进程，未清理镜像/数据或停止其他容器。该导出镜像未完成就绪，真实导出尚未验收；应先解决containerd存储空间再恢复镜像准备。拉取日志在远端 `/tmp/platform-model-export-image-pull.log`。

## 接口参考

- [RF-DETR from_checkpoint与export](https://rfdetr.roboflow.com/latest/reference/rfdetr/)
- [Ultralytics导出参数](https://docs.ultralytics.com/modes/export/)
- [NVIDIA TensorRT文档](https://docs.nvidia.com/deeplearning/tensorrt/latest/)
