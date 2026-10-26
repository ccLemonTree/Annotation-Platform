# 跨服务器 ONNX / TensorRT 导出

训练任务详情 → 导出模型 → TensorRT：选择训练服务器的检查点及ONNX导出GPU，再选择目标服务器和TensorRT构建GPU。切换目标服务器保留源权重路径。两个阶段可指定各自已就绪的镜像；batch、动态profile及分辨率沿用同一份配置。

一个导出任务依次记录 `onnx → transferring → tensorrt`。ONNX阶段使用原训练服务器快照及单独容器ID，目标服务器只读取自动接收的ONNX。源检查点不传输。两阶段GPU统一预留（同卡去重），完成或确认两个容器停止后释放；连接不明时保留占用。

ONNX和外部张量按成功产物清单打包，传输包及每个文件均校验SHA256。SSH-manager先下载到平台临时目录，再上传到目标服务器；传输在后台执行，状态请求不等待大文件。临时中转文件使用后删除，服务器独立任务目录保留。当前传输经过平台所在机器，速度受其网络影响，不是服务器间直接SCP。

目标TensorRT阶段直接使用来源清单里的输入名称和高宽，校验接收文件后调用trtexec，不再依赖Python `onnx`，也不重复重命名/重写源图。`onnx`及RF-DETR导出配套依赖只需要安装在源端导出镜像。

完成后在“模型产物”下载ONNX、外部张量、engine和result.json。engine依赖构建GPU及TensorRT版本。仅ONNX导出固定使用原训练服务器。已有单阶段任务和LoRA合并兼容原行为，无数据库迁移。

验证：

- 后端导出、训练生命周期及下载回归：125 passed，1 skipped（Windows符号链接权限），见 `output/export-target-no-onnx.log`。其中两项显式禁止目标端导入Python onnx，验证TensorRT命令生成及损坏文件提前阻断。
- 新增跨服务器成功、源端失败、传输损坏、中途停止、目标预检查失败、双端停止异常、同卡去重，以及真实ONNX外部张量的打包/接收/重新加载测试。
- 前端 `npm run build`、修改文件oxlint通过。
- `scripts/verify_model_output_ui.cjs` 独立浏览器检查源端A100/目标T4、1/7/8动态batch、切换目标保留检查点、错误保留表单、阶段展示、engine下载、LoRA兼容、YOLO固定batch4和移动端提交。4次隔离提交，真实业务写入0。
- 未在真实GPU上执行YOLO/RF-DETR导出及TensorRT编译；未导出用户业务权重。
