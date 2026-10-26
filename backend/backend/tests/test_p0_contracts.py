from uuid import uuid4

import pytest

from api.deps import get_current_superuser
from api.smarteye_routes import ImportRequest
from models import User, Tenant, Project, Asset, Annotation, TaskType, ProjectStatus
import pytest_asyncio


@pytest_asyncio.fixture
async def foreign_annotation(test_db):
    tenant = Tenant(id=2, tenant_name="other")
    test_db.add(tenant)
    await test_db.flush()
    project = Project(tenant_id=2, project_name="other", task_type=TaskType.DETECTION,
                      status=ProjectStatus.ACTIVE, total_assets=1, annotated_count=0, reviewed_count=0)
    test_db.add(project)
    await test_db.flush()
    asset = Asset(project_id=project.id, original_name="other.jpg", blob_path="other.jpg",
                  content_hash="other", file_size=1, source_type="upload")
    test_db.add(asset)
    await test_db.flush()
    annotation = Annotation(asset_id=asset.id, annotation_data={"boxes": []}, version=1, is_latest=True)
    test_db.add(annotation)
    await test_db.commit()
    return annotation


def test_foreign_annotations_hidden(client, auth_headers, foreign_annotation):
    response = client.get("/api/annotations", headers=auth_headers)
    assert response.json()["total"] == 0
    response = client.get(f"/api/annotations/{foreign_annotation.id}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.parametrize("action", ["approve", "reject", "delete"])
def test_batch_cannot_modify_foreign_annotations(client, auth_headers, foreign_annotation, action):
    response = client.post(f"/api/batch/annotations/{action}", headers=auth_headers,
                           json=[str(foreign_annotation.id)])
    assert response.status_code == 404


def test_project_preserves_labels(client, auth_headers):
    labels = {"labels": ["person", "car"]}
    response = client.post("/api/projects", headers=auth_headers,
                           json={"project_name": "labels", "task_type": "detection", "label_schema": labels})
    assert response.status_code == 201
    assert response.json().get("label_schema") == labels
    response = client.get(f"/api/projects/{response.json()['id']}", headers=auth_headers)
    assert response.json().get("label_schema") == labels


def test_project_task_type_filter(client, auth_headers, test_project):
    response = client.post("/api/projects", headers=auth_headers,
                           json={"project_name": "classification", "task_type": "classification"})
    assert response.status_code == 201
    response = client.get("/api/projects?task_type=classification", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert all(p["task_type"] == "classification" for p in response.json()["items"])


def test_smarteye_import_accepts_uuid():
    project_id = uuid4()
    try:
        request = ImportRequest(task_id=1, project_id=str(project_id))
    except ValueError as error:
        pytest.fail(f"Valid UUID rejected: {error}")
    assert request.project_id == project_id


@pytest.mark.asyncio
async def test_admin_role_is_accepted():
    user = User(role="admin", is_active=True)
    try:
        result = await get_current_superuser(user)
    except AttributeError as error:
        pytest.fail(str(error))
    assert result is user


@pytest.mark.asyncio
async def test_ordinary_user_cannot_use_admin_operations():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await get_current_superuser(User(role="annotator", is_active=True))
    assert error.value.status_code == 403


def test_annotation_save_read_review_delete(client, auth_headers, test_asset, mock_annotation_data):
    response = client.post("/api/annotations", headers=auth_headers,
                           json={"asset_id": str(test_asset.id), "annotation_data": mock_annotation_data})
    assert response.status_code == 201
    annotation = response.json()
    assert annotation["annotator_id"] == 1
    path = f"/api/annotations/{annotation['id']}"
    assert client.get(path, headers=auth_headers).json()["annotation_data"] == mock_annotation_data
    response = client.post(path + "/review", headers=auth_headers, json={"approved": True})
    assert response.status_code == 200
    assert response.json()["reviewer_id"] == 1 and response.json()["review_status"] == "approved"
    assert client.delete(path, headers=auth_headers).status_code == 204
    assert client.get(path, headers=auth_headers).status_code == 404


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/annotations", None),
    ("POST", "/api/annotations", {"asset_id": str(uuid4()), "annotation_data": {"boxes": []}}),
    ("GET", f"/api/annotations/{uuid4()}", None),
    ("DELETE", f"/api/annotations/{uuid4()}", None),
    ("PUT", f"/api/annotations/{uuid4()}", {"annotation_data": {"boxes": []}}),
    ("POST", f"/api/annotations/{uuid4()}/review", {"approved": True}),
    ("GET", "/api/smarteye/platforms", None),
])
def test_annotation_and_smarteye_require_auth(client, method, path, body):
    response = client.request(method, path, json=body) if body is not None else client.request(method, path)
    assert response.status_code == 401


def test_annotation_review_filter(client, auth_headers, test_asset, mock_annotation_data):
    response = client.post("/api/annotations", headers=auth_headers,
                           json={"asset_id": str(test_asset.id), "annotation_data": mock_annotation_data})
    assert response.status_code == 201
    response = client.get("/api/annotations?review_status=approved", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_invalid_uuid_returns_serializable_error(client, auth_headers):
    response = client.get("/api/projects/undefined", headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["success"] is False


def test_validation_response_does_not_echo_password(client):
    secret = "PRIVATE_TEST_PASSWORD" * 10
    response = client.post("/api/auth/register", json={"username": "testnew", "email": "new@example.com",
                                                     "password": secret, "tenant_id": 1})
    assert response.status_code == 422
    assert secret not in response.text
    assert response.json()["code"] == 422


def test_jwt_secret_is_loaded_from_configuration():
    from config.settings import Settings
    secret = "configured-test-key-" * 3
    config = Settings(_env_file=None, JWT_SECRET_KEY=secret)
    assert getattr(config, "JWT_SECRET_KEY", None) == secret


def test_registration_uses_single_tenant(client):
    response = client.post("/api/auth/register", json={"username": "newuser", "email": "new@example.com",
                                                     "password": "testpassword"})
    assert response.status_code == 201
    assert response.json()["tenant_id"] == 1


def test_registration_cannot_select_another_tenant(client):
    response = client.post("/api/auth/register", json={"username": "newuser", "email": "new@example.com",
                                                     "password": "testpassword", "tenant_id": 2})
    assert response.status_code == 400


def test_smarteye_cannot_create_for_another_tenant(client, auth_headers):
    response = client.post("/api/smarteye/download", headers=auth_headers,
                           json={"tenant_id": 2, "platName": "test"})
    assert response.status_code == 403


def test_smarteye_cannot_list_another_tenant(client, auth_headers):
    response = client.get("/api/smarteye/tasks?tenant_id=2", headers=auth_headers)
    assert response.status_code == 403


def test_login_and_invalid_tokens(client, test_user):
    from datetime import timedelta
    from services.auth import create_access_token
    response = client.post("/api/auth/login", data={"username": test_user.username, "password": "testpassword"})
    assert response.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + response.json()["access_token"]}).status_code == 200
    response = client.post("/api/auth/login", data={"username": test_user.username, "password": "wrong"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    expired = create_access_token({"user_id": test_user.id}, expires_delta=timedelta(seconds=-10))
    for token in ("invalid-token", expired):
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 401


@pytest.mark.parametrize("path,expected", [("/api/annotations", 401), ("/missing", 404)])
def test_http_errors_have_consistent_codes(client, path, expected):
    response = client.get(path)
    assert response.status_code == expected
    assert response.json()["success"] is False
    assert response.json()["code"] == expected
    assert response.json()["detail"]


@pytest.mark.parametrize("password", ["x" * 73, "汉" * 30])
def test_registration_rejects_password_over_bcrypt_byte_limit(client, password):
    response = client.post("/api/auth/register", json={"username": "newuser", "email": "new@example.com", "password": password})
    assert response.status_code == 422
    assert password not in response.text


def test_batch_asset_delete_updates_project_count(client, auth_headers, test_asset):
    response = client.post("/api/batch/assets/delete", headers=auth_headers, json=[str(test_asset.id)])
    assert response.status_code == 200
    assert response.json()["affected_count"] == 1
    assert client.get(f"/api/projects/{test_asset.project_id}", headers=auth_headers).json()["total_assets"] == 0


def test_batch_asset_status_contract(client, auth_headers, test_asset):
    response = client.post("/api/batch/assets/update-status?new_status=annotating", headers=auth_headers,
                           json=[str(test_asset.id)])
    assert response.status_code == 200
    assert response.json()["affected_count"] == 1
    assert client.get(f"/api/assets/{test_asset.id}", headers=auth_headers).json()["annotation_status"] == "annotating"


@pytest.mark.parametrize("action", ["delete", "update-status?new_status=completed"])
def test_batch_cannot_modify_foreign_assets(client, auth_headers, foreign_annotation, action):
    response = client.post(f"/api/batch/assets/{action}", headers=auth_headers,
                           json=[str(foreign_annotation.asset_id)])
    assert response.status_code == 404


def test_project_update_delete_contract(client, auth_headers, test_project):
    response = client.put(f"/api/projects/{test_project.id}", headers=auth_headers,
                          json={"status": "archived", "label_schema": {"labels": ["person"]}})
    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert response.json()["label_schema"] == {"labels": ["person"]}
    assert client.delete(f"/api/projects/{test_project.id}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/projects/{test_project.id}", headers=auth_headers).status_code == 404


def test_foreign_asset_content_hidden(client, auth_headers, foreign_annotation):
    response = client.get(f"/api/assets/{foreign_annotation.asset_id}/content", headers=auth_headers)
    assert response.status_code == 404


@pytest.fixture
def foreign_smarteye_task(client):
    from integrations.smarteye.runtime import get_runtime
    get_runtime().create('foreign-task', 'detection', {'status': 'pending'}, {'tenant_id': 2, 'user_id': 2})
    return 'foreign-task'

@pytest.mark.parametrize("endpoint", ["detail", "cancel", "retry"])
def test_foreign_smarteye_task_hidden(client, auth_headers, foreign_smarteye_task, endpoint):
    path = f"/api/smarteye/tasks/{foreign_smarteye_task}"
    response = client.get(path, headers=auth_headers) if endpoint == 'detail' else client.post(
        path + '/' + endpoint, headers=auth_headers)
    assert response.status_code == 404
