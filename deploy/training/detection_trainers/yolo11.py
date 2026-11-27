from pathlib import Path

from .runtime import WEIGHTS, check_batch, read_config, select_weight

YOLO11_SIZES = ("n", "s", "m", "l", "x")


def train_options(config, gpu_count):
    params = dict(config["parameters"])
    size = params.pop("size")
    if size not in YOLO11_SIZES:
        raise ValueError(f"YOLO11 size must be one of {', '.join(YOLO11_SIZES)}")
    model = Path(config.get("model_path") or WEIGHTS / f"yolo11{size}.pt")
    params.update(device=list(range(gpu_count)), data=config["runtime_dataset"],
                  project="/output", name=config["output_subdir"], exist_ok=True)
    return model, params


def main():
    import torch
    from ultralytics import YOLO, settings

    _, config = read_config()
    gpu_count = torch.cuda.device_count()
    weight, options = train_options(config, gpu_count)
    check_batch(options["batch"], gpu_count)
    select_weight(config, weight.name)
    settings.update({"sync": False, "wandb": False, "clearml": False, "comet": False})
    YOLO(str(weight), task="detect").train(**options)


if __name__ == "__main__":
    main()
