"""Check platform paths and category order without importing GPU dependencies."""
import json
from pathlib import Path

import pytest

from detection_trainers.damo_yolo import DAMO_CONFIGS, coco_classes
from detection_trainers.yolo11 import YOLO11_SIZES, train_options


@pytest.mark.parametrize("size", tuple("nsmlx"))
def test_yolo11_selects_the_bundled_detection_weight(size):
    model, options = train_options({
        "parameters": {"size": size, "epochs": 2},
        "runtime_dataset": "/output/prepared-dataset/data.yaml",
        "output_subdir": "train",
    }, gpu_count=2)
    assert model == Path(f"/opt/weights/yolo11{size}.pt")
    assert options == {
        "epochs": 2, "device": [0, 1], "data": "/output/prepared-dataset/data.yaml",
        "project": "/output", "name": "train", "exist_ok": True,
    }
    assert set(YOLO11_SIZES) == set("nsmlx")


def test_damo_uses_train_category_ids_and_validates_the_validation_split(tmp_path):
    assert set(DAMO_CONFIGS) == {"ns", "nm", "nl", "t", "s", "m", "l"}
    categories = [{"id": 9, "name": "smoke"}, {"id": 2, "name": "fire"}]
    for split in ("train", "valid"):
        directory = tmp_path / split
        directory.mkdir()
        (directory / "_annotations.coco.json").write_text(
            json.dumps({"categories": categories}), encoding="utf-8"
        )
    assert coco_classes(tmp_path) == ["fire", "smoke"]
    (tmp_path / "valid" / "_annotations.coco.json").write_text(
        json.dumps({"categories": categories[1:]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="train.*valid"):
        coco_classes(tmp_path)
