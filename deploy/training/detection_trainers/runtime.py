import argparse
import json
from pathlib import Path

WEIGHTS = Path("/opt/weights")


def read_config():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    path = parser.parse_args().config.resolve()
    return path, json.loads(path.read_text(encoding="utf-8"))


def select_weight(config, bundled):
    weight = Path(config.get("model_path") or WEIGHTS / bundled)
    if not weight.is_file():
        raise FileNotFoundError(f"Pretrained checkpoint not found: {weight}")
    return weight


def check_batch(batch, gpu_count):
    if gpu_count < 1 or batch < 1 or batch % gpu_count:
        raise ValueError("Total batch must be positive and divisible by the number of visible GPUs")
