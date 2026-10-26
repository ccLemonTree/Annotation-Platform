import pytest
from pydantic import ValidationError
from schemas.project import ProjectCreate, ProjectUpdate

@pytest.mark.parametrize("labels", ["人", [1], [" "], ["人", "人"], ["人", " 人 "]])
def test_invalid_project_labels_rejected(labels):
    with pytest.raises(ValidationError):
        ProjectUpdate(label_schema={"labels": labels})

def test_project_labels_trim_keep_order_and_other_metadata():
    schema = {"labels": [" 车 ", "人"], "description": "测试"}
    project = ProjectCreate(project_name="类别测试", task_type="detection", label_schema=schema)
    assert project.label_schema == {"labels": ["车", "人"], "description": "测试"}
    assert schema["labels"] == [" 车 ", "人"]

def test_unconfigured_legacy_project_remains_supported():
    assert ProjectUpdate(label_schema=None).label_schema is None
    assert ProjectCreate(project_name="旧项目", task_type="detection").label_schema is None
