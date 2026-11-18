"""Comprehensive scale, anti-deadlock, N+1 elimination, and API contract tests (SQLite isolated)."""
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.dialects import postgresql

from database import PG_SERVER_SETTINGS, build_engine_options
from models import (
    Annotation,
    AnnotationEvent,
    Asset,
    AutomationJob,
    Base,
    ExportSnapshot,
    ImageCollection,
    ImageCollectionMember,
    ImageUploadBatch,
    LibraryImage,
    Project,
    ProjectMember,
    ProjectWorkItem,
    ProjectWorkJob,
    Tenant,
    TrainingAnalysis,
    TrainingTrainer,
    User,
    ValidationItem,
    ValidationRun,
    ValidationSet,
    VectorTestDataset,
    VectorTestIndex,
    VectorTestItem,
    VectorTestModel,
    VectorTestSample,
    VectorTestSampleItem,
)
from services.storage import get_storage


def _make_jpeg_bytes(width=640, height=480, color="navy") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    return buf.getvalue()


def test_engine_pool_and_pg_timeout_options():
    pg_opts = build_engine_options("postgresql+asyncpg://u:p@127.0.0.1:5432/demo")
    assert pg_opts["pool_size"] == 32
    assert pg_opts["max_overflow"] == 32
    assert pg_opts["connect_args"]["server_settings"] == PG_SERVER_SETTINGS
    assert pg_opts["connect_args"]["server_settings"]["idle_in_transaction_session_timeout"] == "60000"
    assert pg_opts["connect_args"]["server_settings"]["lock_timeout"] == "15000"

    sqlite_opts = build_engine_options("sqlite+aiosqlite:///:memory:")
    assert "connect_args" not in sqlite_opts


def test_models_init_exports_all_orm_tables():
    registered = set(Base.metadata.tables.keys())
    expected = {
        "vector_test_models",
        "vector_test_indexes",
        "vector_test_items",
        "vector_test_datasets",
        "vector_test_samples",
        "vector_test_sample_items",
        "training_trainers",
        "training_analyses",
        "image_upload_batches",
        "image_upload_batch_files",
    }
    assert expected <= registered
    assert all(
        cls is not None
        for cls in (
            VectorTestModel,
            VectorTestIndex,
            VectorTestItem,
            VectorTestDataset,
            VectorTestSample,
            VectorTestSampleItem,
            TrainingTrainer,
            TrainingAnalysis,
            ImageUploadBatch,
        )
    )
    assert Annotation._asset_project_id(None) is None


@pytest.mark.asyncio
async def test_image_content_preview_and_rollback(client, auth_headers, test_db, test_user):
    raw = _make_jpeg_bytes(1200, 900)
    digest = hashlib.sha256(raw).hexdigest()
    blob_path = f"images/{digest[:2]}/{digest}.jpg"
    await get_storage().save_file(BytesIO(raw), blob_path)
    img = LibraryImage(
        tenant_id=test_user.tenant_id,
        blob_path=blob_path,
        content_hash=digest,
        original_name="large_cam.jpg",
        file_size=len(raw),
        width=1200,
        height=900,
        format="JPEG",
        source_type="upload",
    )
    test_db.add(img)
    await test_db.commit()

    preview_resp = client.get(f"/api/images/{img.id}/content?preview=true", headers=auth_headers)
    assert preview_resp.status_code == 200
    assert preview_resp.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(preview_resp.content)) as thumb:
        assert max(thumb.size) == 480
    assert len(preview_resp.content) < len(raw)

    full_resp = client.get(f"/api/images/{img.id}/content", headers=auth_headers)
    assert full_resp.status_code == 200
    assert full_resp.content == raw


@pytest.mark.asyncio
async def test_asset_neighbors_uses_keyset_without_window_functions(client, auth_headers, test_db, test_engine, test_project):
    from datetime import datetime

    ids = [UUID(f"cccccccc-0000-4000-8000-{i + 1:012x}") for i in range(12)]
    for i, aid in enumerate(ids):
        test_db.add(
            Asset(
                id=aid,
                project_id=test_project.id,
                original_name=f"frame_{i:02d}.jpg",
                blob_path="fixture.jpg",
                content_hash=f"hash-{i}",
                file_size=10,
                width=64,
                height=48,
                source_type="upload",
                created_at=datetime(2026, 11, 18, 8, 0, i // 3),
            )
        )
    await test_db.commit()

    executed_sql: list[str] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        executed_sql.append(statement.upper())

    event.listen(test_engine.sync_engine, "before_cursor_execute", capture_sql)
    try:
        listing = client.get(
            "/api/assets",
            headers=auth_headers,
            params={"project_id": str(test_project.id), "page": 1, "page_size": 50},
        ).json()["items"]
        ordered_ids = [row["id"] for row in listing]
        executed_sql.clear()

        for pos_idx in (0, 5, 11):
            resp = client.get(f"/api/assets/{ordered_ids[pos_idx]}/neighbors", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {
                "position": pos_idx + 1,
                "total": 12,
                "previous_id": ordered_ids[pos_idx - 1] if pos_idx > 0 else None,
                "next_id": ordered_ids[pos_idx + 1] if pos_idx < 11 else None,
            }
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", capture_sql)

    for sql in executed_sql:
        assert "ROW_NUMBER(" not in sql
        assert "LAG(" not in sql
        assert "LEAD(" not in sql


def test_large_id_filter_helper_pg_any_and_sqlite_chunking():
    from services.image_library import id_in_condition, iter_id_conditions

    sample_ids = [uuid4() for _ in range(1205)]
    pg_db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()))
    pg_cond = id_in_condition(pg_db, Asset.id, sample_ids)
    compiled_pg = str(pg_cond.compile(dialect=postgresql.dialect()))
    assert "= ANY" in compiled_pg
    assert len(iter_id_conditions(pg_db, Asset.id, sample_ids)) == 1

    sqlite_db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    chunks = iter_id_conditions(sqlite_db, Asset.id, sample_ids, chunk_size=500)
    assert len(chunks) == 3


@pytest.mark.asyncio
async def test_project_used_labels_and_delete_project_cleanup_and_guard(
    client, auth_headers, test_db, test_engine, test_project, test_user
):
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_project.label_schema = {"labels": ["person", "car", "helmet"]}
    a1 = Asset(
        project_id=test_project.id,
        original_name="img1.jpg",
        blob_path="images/00/img1.jpg",
        content_hash="u1",
        file_size=10,
        width=100,
        height=100,
        source_type="upload",
    )
    a2 = Asset(
        project_id=test_project.id,
        original_name="img2.jpg",
        blob_path="images/00/img2.jpg",
        content_hash="u2",
        file_size=10,
        width=100,
        height=100,
        source_type="upload",
    )
    test_db.add_all([a1, a2])
    await test_db.flush()
    test_db.add_all(
        [
            Annotation(
                project_id=test_project.id,
                asset_id=a1.id,
                version=1,
                is_latest=False,
                annotation_data={"boxes": [{"label": "obsolete", "x": 1, "y": 1, "width": 10, "height": 10}]},
            ),
            Annotation(
                project_id=test_project.id,
                asset_id=a1.id,
                version=2,
                is_latest=True,
                annotation_data={
                    "boxes": [
                        {"label": "person", "x": 1, "y": 1, "width": 10, "height": 10},
                        {"label": "class_0", "yolo_class_id": 0, "x": 2, "y": 2, "width": 8, "height": 8},
                    ],
                    "labels": ["outdoor"],
                },
            ),
            Annotation(
                project_id=test_project.id,
                asset_id=a2.id,
                version=1,
                is_latest=True,
                annotation_data={"boxes": [{"label": "car", "x": 5, "y": 5, "width": 12, "height": 12}]},
            ),
        ]
    )
    await test_db.commit()

    used_resp = client.get(f"/api/projects/{test_project.id}/used-labels", headers=auth_headers)
    assert used_resp.status_code == 200
    assert used_resp.json() == {"labels": ["car", "outdoor", "person"]}

    # Foreign tenant receives 403
    test_db.add(Tenant(id=77, tenant_name="foreign_t"))
    await test_db.flush()
    foreign_proj = Project(tenant_id=77, project_name="fp", task_type="detection")
    test_db.add(foreign_proj)
    await test_db.commit()
    assert client.get(f"/api/projects/{foreign_proj.id}/used-labels", headers=auth_headers).status_code == 403

    # Active background task blocks project deletion with 409
    auto_job = AutomationJob(
        tenant_id=test_user.tenant_id,
        project_id=test_project.id,
        user_id=test_user.id,
        kind="preannotate",
        status="running",
        config={},
        models={},
        label_schema={},
    )
    test_db.add(auto_job)
    await test_db.commit()
    blocked = client.delete(f"/api/projects/{test_project.id}", headers=auth_headers)
    assert blocked.status_code == 409

    # Finish task, create crop directory and snapshot files, then verify clean deletion
    auto_job.status = "completed"
    storage = get_storage()
    crop_rel = f"project-crops/{test_project.id}/aa/crop1.png"
    snap_zip = f"exports/snap_{test_project.id}.zip"
    snap_manifest = f"exports/manifests/snap_{test_project.id}.json"
    await storage.save_file(BytesIO(b"crop"), crop_rel)
    await storage.save_file(BytesIO(b"zip"), snap_zip)
    await storage.save_file(BytesIO(b"{}"), snap_manifest)
    snap = ExportSnapshot(
        project_id=test_project.id,
        user_id=test_user.id,
        export_format="yolo",
        blob_path=snap_zip,
        content_hash="abc",
        manifest_path=snap_manifest,
    )
    test_db.add(snap)
    await test_db.commit()

    crop_dir = storage.resolve_path(f"project-crops/{test_project.id}")
    assert crop_dir.is_dir()
    assert await storage.file_exists(snap_zip)
    assert await storage.file_exists(snap_manifest)

    deleted = client.delete(f"/api/projects/{test_project.id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert not crop_dir.exists()
    assert not await storage.file_exists(snap_zip)
    assert not await storage.file_exists(snap_manifest)


@pytest.mark.asyncio
async def test_annotations_list_contract_filters_and_joined_asset_fields(
    client, auth_headers, test_db, test_project
):
    a1 = Asset(
        project_id=test_project.id,
        original_name="gate_helmet_01.jpg",
        blob_path="a1.jpg",
        content_hash="c1",
        file_size=10,
        width=100,
        height=100,
        source_type="upload",
    )
    a2 = Asset(
        project_id=test_project.id,
        original_name="road_truck_02.png",
        blob_path="a2.png",
        content_hash="c2",
        file_size=10,
        width=100,
        height=100,
        source_type="model_crop",
    )
    test_db.add_all([a1, a2])
    await test_db.flush()
    test_db.add_all(
        [
            Annotation(
                project_id=test_project.id,
                asset_id=a1.id,
                version=1,
                is_latest=True,
                annotation_source="manual",
                review_status="pending",
                annotation_data={"boxes": [{"label": "helmet", "x": 1, "y": 1, "width": 10, "height": 10}]},
            ),
            Annotation(
                project_id=test_project.id,
                asset_id=a2.id,
                version=1,
                is_latest=True,
                annotation_source="auto",
                review_status="approved",
                annotation_data={"boxes": [{"label": "truck", "x": 2, "y": 2, "width": 20, "height": 20}]},
            ),
        ]
    )
    await test_db.commit()

    all_resp = client.get(
        "/api/annotations", headers=auth_headers, params={"project_id": str(test_project.id)}
    ).json()
    assert all_resp["total"] == 2
    by_asset = {item["asset_id"]: item for item in all_resp["items"]}
    assert by_asset[str(a1.id)]["asset_original_name"] == "gate_helmet_01.jpg"
    assert by_asset[str(a1.id)]["asset_source_type"] == "upload"
    assert by_asset[str(a2.id)]["asset_original_name"] == "road_truck_02.png"
    assert by_asset[str(a2.id)]["asset_source_type"] == "model_crop"

    q_resp = client.get(
        "/api/annotations",
        headers=auth_headers,
        params={"project_id": str(test_project.id), "q": "HELMET"},
    ).json()
    assert q_resp["total"] == 1 and q_resp["items"][0]["asset_id"] == str(a1.id)

    lbl_resp = client.get(
        "/api/annotations",
        headers=auth_headers,
        params={"project_id": str(test_project.id), "label": "truck"},
    ).json()
    assert lbl_resp["total"] == 1 and lbl_resp["items"][0]["asset_id"] == str(a2.id)

    src_resp = client.get(
        "/api/annotations",
        headers=auth_headers,
        params={"project_id": str(test_project.id), "annotation_source": "manual"},
    ).json()
    assert src_resp["total"] == 1 and src_resp["items"][0]["asset_id"] == str(a1.id)


@pytest.mark.asyncio
async def test_coco_single_file_unlocked_and_delete_snapshot_removes_manifest(
    client, auth_headers, test_db, test_engine, test_project, test_asset, test_user
):
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_project.label_schema = {"labels": ["person"]}
    test_db.add(
        Annotation(
            project_id=test_project.id,
            asset_id=test_asset.id,
            version=1,
            is_latest=True,
            review_status="approved",
            reviewer_id=test_user.id,
            annotation_data={"boxes": [{"label": "person", "x": 1, "y": 1, "width": 10, "height": 10}]},
        )
    )
    storage = get_storage()
    zip_key = "exports/del_test.zip"
    manifest_key = "exports/manifests/del_test.json"
    await storage.save_file(BytesIO(b"zipdata"), zip_key)
    await storage.save_file(BytesIO(b'{"assets":[]}'), manifest_key)
    snap = ExportSnapshot(
        project_id=test_project.id,
        user_id=test_user.id,
        export_format="coco",
        blob_path=zip_key,
        content_hash="h123",
        manifest_path=manifest_key,
    )
    test_db.add(snap)
    await test_db.commit()

    coco_resp = client.get(
        f"/api/export/projects/{test_project.id}/export",
        headers=auth_headers,
        params={"format": "coco", "package": "false", "approved_only": "true"},
    )
    assert coco_resp.status_code == 200
    assert len(coco_resp.json()["annotations"]) == 1

    del_resp = client.delete(f"/api/export/snapshots/{snap.id}", headers=auth_headers)
    assert del_resp.status_code == 204
    assert not await storage.file_exists(zip_key)
    assert not await storage.file_exists(manifest_key)


@pytest.mark.asyncio
async def test_project_work_retry_cap_and_unexpected_exception_containment(
    test_engine, test_db, test_project, test_user, monkeypatch
):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models.model_service import ModelService
    from schemas.project_work import WorkflowCreate
    from services import project_work, project_workflow

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    raw = _make_jpeg_bytes(64, 48)
    digest = hashlib.sha256(raw).hexdigest()
    await get_storage().save_file(BytesIO(raw), "images/pw/a.jpg")
    asset = Asset(
        project_id=test_project.id,
        original_name="pw.jpg",
        blob_path="images/pw/a.jpg",
        content_hash=digest,
        file_size=len(raw),
        width=64,
        height=48,
        source_type="upload",
    )
    det_model = ModelService(
        tenant_id=test_user.tenant_id,
        key="pw-det",
        name="Det",
        kind="detection",
        protocol="model_service",
        base_url="http://det.local",
        endpoint="/detect",
        labels=["person"],
        label_mapping={},
        connection_check={"ok": True, "checked_revision": 1},
    )
    test_db.add_all([asset, det_model])
    await test_db.commit()

    now = 10000
    monkeypatch.setattr(project_work, "time", lambda: now)

    calls = 0

    async def always_502(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise project_work.HTTPException(502, "下游网关持续故障")

    monkeypatch.setattr(project_work, "infer", always_502)

    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as db:
        proj = await db.get(Project, test_project.id)
        (job,) = await project_workflow.create(
            db, proj, WorkflowCreate(steps=[{"operation": "crop", "model_id": det_model.id}]), test_user.id
        )

    async with httpx.AsyncClient() as http_client:
        for expected_attempt in range(1, project_work.MAX_AUTO_RETRY_ATTEMPTS + 1):
            assert await project_work.process_one(sessions, http_client)
            async with sessions() as db:
                item = await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id == job.id))
                assert item.attempt == expected_attempt
                if expected_attempt < project_work.MAX_AUTO_RETRY_ATTEMPTS:
                    assert item.status == "retry_wait"
                else:
                    assert item.status == "failed"
                    assert "retry_at" not in (item.result or {})
            now += 600

        # Once capped at 5 attempts, worker stops retrying forever and workflow history can be deleted
        assert not await project_work.process_one(sessions, http_client)
        assert calls == project_work.MAX_AUTO_RETRY_ATTEMPTS

    async with sessions() as db:
        proj = await db.get(Project, test_project.id)
        await project_workflow.delete_history(db, proj, job.id)

    # Unexpected RuntimeError is caught and persisted as failed without crashing worker loop
    async def boom_infer(*args, **kwargs):
        raise TypeError("unexpected NoneType iteration")

    monkeypatch.setattr(project_work, "infer", boom_infer)
    async with sessions() as db:
        proj = await db.get(Project, test_project.id)
        (job2,) = await project_workflow.create(
            db, proj, WorkflowCreate(steps=[{"operation": "crop", "model_id": det_model.id}]), test_user.id
        )
    async with httpx.AsyncClient() as http_client:
        assert await project_work.process_one(sessions, http_client)
    async with sessions() as db:
        item2 = await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id == job2.id))
        assert item2.attempt == 1
        assert item2.error is not None


@pytest.mark.asyncio
async def test_batch_attach_assign_and_workflow_list_avoid_n_plus_1(
    client, auth_headers, test_db, test_engine, test_project, test_user
):
    from services.image_library import assign_collection, attach_images

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    imgs = [
        LibraryImage(
            tenant_id=test_user.tenant_id,
            blob_path=f"images/00/b{i}.jpg",
            content_hash=f"batch-hash-{i}",
            original_name=f"b{i}.jpg",
            file_size=10,
            width=64,
            height=48,
            source_type="upload",
        )
        for i in range(40)
    ]
    test_db.add_all(imgs)
    for w_idx in range(8):
        root_id = uuid4()
        for step_idx in range(3):
            jid = root_id if step_idx == 0 else uuid4()
            job = ProjectWorkJob(
                id=jid,
                project_id=test_project.id,
                user_id=test_user.id,
                operation="crop",
                model={"id": str(uuid4()), "revision": 1, "config": {"name": "M"}},
                options={
                    "workflow_id": str(root_id),
                    "workflow_step": step_idx,
                    "workflow_started": True,
                    "workflow_name": f"WF-{w_idx}",
                    "workflow_streaming": True,
                },
            )
            test_db.add(job)
            await test_db.flush()
            test_db.add(ProjectWorkItem(job_id=jid, asset_id=None, content_hash="x", status="completed"))
    await test_db.commit()

    statements: list[str] = []

    def count_stmt(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", count_stmt)
    try:
        assets, created = await attach_images(test_db, test_project.id, test_user.tenant_id, [i.id for i in imgs])
        assert len(assets) == len(created) == 40
        assert len(statements) <= 10

        statements.clear()
        _, added = await assign_collection(test_db, test_user.tenant_id, [i.id for i in imgs], "批处理图集")
        assert added == 40
        assert len(statements) <= 10
        await test_db.commit()

        statements.clear()
        wf_resp = client.get(f"/api/projects/{test_project.id}/work/workflows", headers=auth_headers)
        assert wf_resp.status_code == 200
        assert len(wf_resp.json()) == 8
        # Previously 8 workflows * 3 steps * ~4 queries = ~96 queries; now bounded constant queries!
        assert len(statements) <= 12
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", count_stmt)


@pytest.mark.asyncio
async def test_stats_sql_group_by_and_validation_deferred_samples(
    client, auth_headers, test_db, test_engine, test_project, test_user
):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models.model_service import ModelService
    from services import validation

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    raw = _make_jpeg_bytes(64, 48)
    digest = hashlib.sha256(raw).hexdigest()
    blob_path = f"images/{digest[:2]}/{digest}.jpg"
    await get_storage().save_file(BytesIO(raw), blob_path)

    test_project.label_schema = {"labels": ["person", "car"]}
    a1 = Asset(
        project_id=test_project.id,
        original_name="s1.jpg",
        blob_path=blob_path,
        content_hash=digest,
        file_size=len(raw),
        width=64,
        height=48,
        format="JPEG",
        source_type="upload",
        annotation_status="reviewed",
        quality_score=90,
        device_info={"device_id": "cam-01"},
    )
    test_db.add(a1)
    await test_db.flush()
    test_db.add(
        Annotation(
            project_id=test_project.id,
            asset_id=a1.id,
            version=1,
            is_latest=True,
            review_status="approved",
            reviewer_id=test_user.id,
            annotation_data={"boxes": [{"label": "person", "x": 1, "y": 2, "width": 10, "height": 20}]},
        )
    )
    model = ModelService(
        tenant_id=test_user.tenant_id,
        key="val-det-scale",
        name="Detector",
        kind="detection",
        protocol="model_service",
        base_url="http://det.test",
        endpoint="/detect",
        labels=[],
        label_mapping={},
    )
    test_db.add(model)
    await test_db.commit()

    dist = client.get(f"/api/stats/projects/{test_project.id}/distribution", headers=auth_headers).json()
    assert dist["boxes_by_label"] == {"person": 1}
    assert dist["images_by_device"] == {"cam-01": 1}
    assert dist["images_by_quality"] == {"90": 1}

    p_stats = client.get(f"/api/stats/projects/{test_project.id}/stats", headers=auth_headers).json()
    assert p_stats["reviewed_count"] >= 1
    assert client.get("/api/stats/tenant/stats", headers=auth_headers).status_code == 200
    assert client.get("/api/stats/dashboard", headers=auth_headers).status_code == 200

    # ValidationSet list_sets defers samples column, and ValidationItem stores per-item _sample metadata
    vset = client.post(
        "/api/validation/sets",
        headers=auth_headers,
        json={"name": "ScaleSet", "project_id": str(test_project.id)},
    ).json()
    vrun = client.post(
        "/api/validation/runs",
        headers=auth_headers,
        json={"set_id": vset["id"], "model_id": str(model.id), "config": {}},
    ).json()

    selects: list[str] = []

    def track_selects(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", track_selects)
    try:
        sets_page = client.get("/api/validation/sets", headers=auth_headers).json()
        assert sets_page["total"] == 1
        assert not any("validation_sets.samples" in s for s in selects)

        selects.clear()

        async def fake_infer(cfg, content, w, h):
            return {"boxes": [{"label": "person", "x": 1, "y": 2, "width": 10, "height": 20, "score": 0.95}]}

        monkeypatch_orig = validation.infer
        validation.infer = fake_infer
        try:
            assert await validation.process_one(async_sessionmaker(test_engine, expire_on_commit=False))
        finally:
            validation.infer = monkeypatch_orig
        assert not any(
            "validation_sets.samples" in s and "JSON_EXTRACT" not in s.upper() and "->" not in s
            for s in selects
        )

        items_resp = client.get(f"/api/validation/runs/{vrun['id']}/items", headers=auth_headers).json()
        item_row = items_resp["items"][0]
        assert "_sample" not in item_row["evaluation"]

        selects.clear()
        img_resp = client.get(
            f"/api/validation/runs/{vrun['id']}/items/{item_row['id']}/image", headers=auth_headers
        )
        assert img_resp.status_code == 200 and img_resp.content == raw
        assert not any("validation_sets.samples" in s for s in selects)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", track_selects)


def test_alembic_059_migration_upgrade_and_downgrade(tmp_path):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        "migration_059",
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "059_db_scale_indexes.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "059"
    assert mod.down_revision == "058"

    db_file = tmp_path / "alembic_059.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    try:
        Base.metadata.create_all(sync_engine)
        with sync_engine.begin() as conn:
            # Drop newly added indexes first so upgrade() creates them cleanly from 058 state
            for idx_name in (
                "ix_assets_project_created",
                "ix_assets_project_status_created",
                "ix_assets_project_source_created",
                "ix_assets_project_lower_name",
                "ix_annotations_project_id",
                "ix_automation_jobs_project_id",
                "ix_image_semantic_items_image_id",
                "ix_image_upload_items_image_id",
                "ix_vector_test_items_asset_id",
                "ix_vector_test_samples_parent_id",
                "ix_vector_test_sample_items_asset_id",
                "ix_automation_items_ann_review",
                "ix_ann_events_project_created",
            ):
                conn.exec_driver_sql(f"DROP INDEX IF EXISTS {idx_name}")
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
            asset_indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list('assets')").fetchall()}
            assert "ix_assets_project_created" in asset_indexes
            assert "ix_assets_project_lower_name" in asset_indexes
            assert "idx_asset_project" not in asset_indexes

            with Operations.context(ctx):
                mod.downgrade()
            asset_indexes_after = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list('assets')").fetchall()}
            assert "idx_asset_project" in asset_indexes_after
            assert "ix_assets_project_created" not in asset_indexes_after
    finally:
        sync_engine.dispose()
