"""Shared image references, not the obsolete simulated SmartEye tables."""
import pytest

def upload(client, headers, project_id, data, name="sample.jpg"):
    return client.post(f"/api/assets/upload?project_id={project_id}", headers=headers,
                       files={"file": (name, data, "image/jpeg")})

def test_two_projects_share_one_image(client, auth_headers, test_project, mock_image_data, tmp_path):
    data = mock_image_data.getvalue()
    other = client.post("/api/projects", headers=auth_headers,
                        json={"project_name": "second", "task_type": "detection"}).json()["id"]
    first = upload(client, auth_headers, test_project.id, data)
    second = upload(client, auth_headers, other, data, "renamed.jpg")
    assert first.status_code == second.status_code == 201
    assert first.json()["blob_path"] == second.json()["blob_path"]
    assert len(list((tmp_path / "storage").rglob("*.jpg"))) == 1
    first_id, second_id = first.json()["asset_id"], second.json()["asset_id"]
    assert client.get(f"/api/assets/{second_id}/content", headers=auth_headers).content == data
    assert client.delete(f"/api/assets/{first_id}", headers=auth_headers).status_code == 204
    response = client.get(f"/api/assets/{second_id}/content", headers=auth_headers)
    assert response.status_code == 200 and response.content == data
    assert client.get(f"/api/assets/{second_id}", headers=auth_headers).json()["width"] == 64

def test_duplicate_batch_returns_same_asset(client, auth_headers, test_project, mock_image_data):
    data = mock_image_data.getvalue()
    response = client.post(f"/api/assets/batch-upload?project_id={test_project.id}", headers=auth_headers,
                           files=[("files", ("one.jpg", data, "image/jpeg")),
                                  ("files", ("two.jpg", data, "image/jpeg"))])
    assert response.status_code == 201
    assert response.json()[0]["asset_id"] == response.json()[1]["asset_id"]
    assets = client.get(f"/api/assets?project_id={test_project.id}", headers=auth_headers).json()
    assert assets["total"] == 1

def test_content_requires_auth(client, test_asset):
    assert client.get(f"/api/assets/{test_asset.id}/content").status_code == 401

def test_invalid_image_rejected(client, auth_headers, test_project):
    response = upload(client, auth_headers, test_project.id, b"not an image")
    assert response.status_code == 400


def test_truncated_jpeg_rejected(client, auth_headers, test_project, mock_image_data):
    response = upload(client, auth_headers, test_project.id, mock_image_data.getvalue()[:-30])
    assert response.status_code == 400


def test_pixel_limit_warning_rejected(client, auth_headers, test_project, mock_image_data, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 2000)
    response = upload(client, auth_headers, test_project.id, mock_image_data.getvalue())
    assert response.status_code == 400

def test_oversize_image_rejected(client, auth_headers, test_project, mock_image_data, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE", 20)
    response = upload(client, auth_headers, test_project.id, mock_image_data.getvalue())
    assert response.status_code == 413

def test_filename_is_metadata_not_storage_path(client, auth_headers, test_project, mock_image_data):
    response = upload(client, auth_headers, test_project.id, mock_image_data.getvalue(), "../../escape.jpg")
    assert response.status_code == 201
    assert response.json()["original_name"] == "escape.jpg"
    assert ".." not in response.json()["blob_path"]


def test_invalid_batch_rolls_back_asset_metadata(client, auth_headers, test_project, mock_image_data):
    response = client.post(f"/api/assets/batch-upload?project_id={test_project.id}", headers=auth_headers,
                           files=[("files", ("good.jpg", mock_image_data.getvalue(), "image/jpeg")),
                                  ("files", ("bad.jpg", b"broken", "image/jpeg"))])
    assert response.status_code == 400
    assert client.get(f"/api/assets?project_id={test_project.id}", headers=auth_headers).json()["total"] == 0
    assert client.get(f"/api/projects/{test_project.id}", headers=auth_headers).json()["total_assets"] == 0

