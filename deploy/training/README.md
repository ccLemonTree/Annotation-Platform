# YOLO11 与 DAMO-YOLO 检测训练镜像

创建 Docker 训练任务时选择 YOLO11 或 DAMO-YOLO，再在「模型尺寸」字段选择尺寸。YOLO11 支持 n、s、m、l、x，使用 YOLO 冻结包。DAMO-YOLO 支持 Ns、Nm、Nl、T、S、M、L，可直接选择 YOLO 或 COCO 冻结包。YOLO 标注在任务输出目录内转换为 COCO，原冻结包只读。基础权重路径留空时，训练器加载镜像里的对应权重。批次大小是所选 GPU 合计值，须能被 GPU 数整除。

## 构建

在构建上下文根目录保留本目录的两个 Dockerfile 和 `detection_trainers/`。将 [tinyvision/DAMO-YOLO 源码](https://github.com/tinyvision/DAMO-YOLO/tree/319572eef367340267ab6ab8ae253527a71d7c3a) 的 `damo/`、`configs/` 和 `LICENSE` 放到 `damo-yolo/source/`。按 `weight-sources.txt` 下载对应权重，分别放到 `yolo11/weights/` 和 `damo-yolo/weights/`。两目录各放置一份只含本系列权重文件名与 SHA-256 的 `SHA256SUMS`，构建时会校验。

```sh
docker build -f Dockerfile.yolo11 -t aisfh5.com:9091/train/yolo11:20261126-v1 .
docker build -f Dockerfile.damo-yolo -t aisfh5.com:9091/train/damo-yolo:20261126-v1 .
```

YOLO11 使用 [Ultralytics v8.3.0 检测权重](https://github.com/ultralytics/assets/releases/tag/v8.3.0)。DAMO-YOLO 的 Ns、Nm、Nl、T、S、M 使用 LibreYOLO 保存的 [上游 COCO 权重](https://huggingface.co/LibreYOLO)。L 使用 IISc AIM 的 Apache-2.0 [UVH-26 DAMO-YOLO-L 检测权重](https://huggingface.co/iisc-aim/UVH-26)。DAMO-YOLO 微调沿用上游加载逻辑，从权重初始化骨干与颈部，并按数据集类别初始化检测头。

## 平台配置

在「管理训练器 → 内置训练器」修改新任务的默认模型尺寸和训练参数。启动前确认所选 Docker 训练服务器已拉取镜像。
