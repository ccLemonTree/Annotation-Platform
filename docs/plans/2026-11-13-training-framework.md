# 可登记训练器实施计划

**目标**　管理员登记一次容器训练程序，即可在训练页创建和管理该程序的任务。

1. 在租户内保存训练器的名称、镜像、Python 模块、冻结格式和默认参数。验证权限、参数校验及登记变更后的任务配置。
2. 由 Docker 执行器准备冻结包，并运行镜像内模块。验证 YOLO、COCO、SFT 和向量数据入口，以及旧任务的脚本和启动行为。
3. 在训练页登记并选择训练器，按登记格式筛选数据。验证表单、构建和本地回归，再核对 115 的活动任务与发布条件。

**接入约定**　容器须包含 Python 3.9+，训练模块须安装到该解释器的环境中。YOLO 和 COCO 镜像还须安装 PyYAML。平台以 `python3 -I -u -m <模块> --config /output/run_config.json` 启动训练。模块从配置读取 `runtime_dataset`、`model_path`、`parameters` 和 `output_subdir`，将权重写入 `/output/<output_subdir>`，日志写到标准输出。退出码为 0 且产物目录含权重文件（如 `.pt`、`.safetensors` 或 `.ckpt`）时，任务标记为完成。容器禁网，请在镜像内预装依赖，在训练服务器准备权重。

**范围**　新增训练器使用已配置的 Docker 训练服务器及现有资源管理流程。嘉善 K8s 使用已有的分布式训练器。首版沿用冻结导出支持的训练格式。

| 登记格式 | `runtime_dataset` |
| --- | --- |
| YOLO | `/output/prepared-dataset/data.yaml` |
| COCO | `/output/prepared-dataset`，含 `train`、`valid` 及可选的 `test` |
| SFT 与向量 | `/output/dataset/train.jsonl`，图片引用转换为容器内绝对路径 |

使用管理员账号进入 **项目 → 导出与训练 → 管理训练器**。登记后，在 **创建训练任务** 中选择该训练器、冻结版本、服务器和 GPU。任务可覆盖登记的默认参数，并沿用预检查、启动、停止、日志和产物下载流程。

镜像内的入口可按以下方式对接，将 `fit` 替换为训练程序自己的入口。对于 COCO，数据根目录下的划分标注文件为 `_annotations.coco.json`。对于向量训练，模块按所选格式处理正负样本和批内负样本策略。

```python
import argparse
import json
from pathlib import Path

from my_trainer.engine import fit

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path, required=True)
args = parser.parse_args()
config = json.loads(args.config.read_text(encoding="utf-8"))
output = Path("/output") / config["output_subdir"]
output.mkdir(parents=True, exist_ok=True)
fit(dataset=config["runtime_dataset"], model=config["model_path"] or None,
    output=output, parameters=config["parameters"])
```
