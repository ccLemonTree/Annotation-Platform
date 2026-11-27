import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from .runtime import check_batch, read_config, select_weight

DAMO_ROOT = Path("/opt/damo")
DAMO_CONFIGS = {
    "ns": "damoyolo_tinynasL18_Ns", "nm": "damoyolo_tinynasL18_Nm",
    "nl": "damoyolo_tinynasL20_Nl", "t": "damoyolo_tinynasL20_T",
    "s": "damoyolo_tinynasL25_S", "m": "damoyolo_tinynasL35_M",
    "l": "damoyolo_tinynasL45_L",
}
COCO_ANNOTATIONS = "_annotations.coco.json"


def coco_classes(root):
    train, valid = (
        json.loads((root / split / COCO_ANNOTATIONS).read_text(encoding="utf-8"))["categories"]
        for split in ("train", "valid")
    )
    category_names = {row["id"]: row["name"] for row in train}
    if not category_names or category_names != {row["id"]: row["name"] for row in valid}:
        raise ValueError("COCO train and valid must declare the same nonempty category mapping")
    return [category_names[key] for key in sorted(category_names)]


def train_worker(config, model_name, weight, rank):
    import torch
    from damo.apis import Trainer
    from damo.config.base import parse_config
    from damo.config.paths_catalog import DatasetCatalog

    params = config["parameters"]
    dataset = Path(config["runtime_dataset"])
    classes = coco_classes(dataset)
    os.chdir(DAMO_ROOT)
    recipe = parse_config(str(DAMO_ROOT / "configs" / f"{model_name}.py"))
    for split, names in zip(("train", "valid"), (recipe.dataset.train_ann, recipe.dataset.val_ann)):
        folder = dataset / split
        DatasetCatalog.DATASETS[names[0]] = {
            "img_dir": str(folder), "ann_file": str(folder / COCO_ANNOTATIONS),
        }
    recipe.dataset.class_names = classes
    recipe.model.head.num_classes = len(classes)
    recipe.miscs.output_dir = "/output"
    recipe.miscs.exp_name = config["output_subdir"]
    recipe.miscs.num_workers = params["workers"]
    recipe.miscs.eval_interval_epochs = min(params["eval_period"], params["epochs"])
    recipe.miscs.ckpt_interval_epochs = params["save_period"]
    recipe.train.finetune_path = str(weight)
    recipe.train.total_epochs = params["epochs"]
    recipe.train.batch_size = params["batch"]
    recipe.test.batch_size = params["batch"]
    recipe.train.warmup_epochs = min(recipe.train.warmup_epochs, params["epochs"] - 1)
    recipe.train.no_aug_epochs = min(recipe.train.no_aug_epochs,
                                    params["epochs"] - 1 - recipe.train.warmup_epochs)
    torch.cuda.set_device(rank)
    torch.manual_seed(params["seed"] + rank)
    torch.distributed.init_process_group(backend="nccl")
    try:
        Trainer(recipe, SimpleNamespace(local_rank=rank)).train(rank)
    finally:
        torch.distributed.destroy_process_group()


def main():
    import torch

    path, config = read_config()
    params = config["parameters"]
    size = params["size"]
    if size not in DAMO_CONFIGS:
        raise ValueError(f"DAMO-YOLO size must be one of {', '.join(DAMO_CONFIGS)}")
    weight = select_weight(config, f"damo-{size}.pth")
    gpu_count = torch.cuda.device_count()
    check_batch(params["batch"], gpu_count)
    if "LOCAL_RANK" in os.environ:
        train_worker(config, DAMO_CONFIGS[size], weight, int(os.environ["LOCAL_RANK"]))
        return
    subprocess.run(
        [sys.executable, "-I", "-u", "-m", "torch.distributed.run", "--standalone",
         "--nproc-per-node", str(gpu_count), "--module", "detection_trainers.damo_yolo",
         "--config", str(path)], check=True,
    )


if __name__ == "__main__":
    main()
