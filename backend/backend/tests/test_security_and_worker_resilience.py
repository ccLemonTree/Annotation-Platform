"""Regression tests for sandbox hardening, worker resilience, streaming tickets, auth controls, SSRF, and dry-run previews."""
import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from time import time
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


def _make_png(width: int = 64, height: int = 48, color=(80, 140, 200)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest_asyncio.fixture
async def extra_tables(test_engine):
    from models import ModelService, ObjectAttributeTemplate
    from models.gpu_server import GpuServer
    from models.image_semantic import ImageSemanticItem, ImageSemanticJob, ImageSemanticVector
    from models.model_deployment import DeploymentLease, DeploymentTarget, ModelDeployment, ModelRelease
    from models.model_repository import ModelRepositoryItem
    from models.project_work import ProjectWorkItem, ProjectWorkJob
    from models.training_run import TrainingAnalysis, TrainingGpuHold, TrainingRun, TrainingTrainer
    from models.validation import ValidationItem, ValidationRun, ValidationSet
    from models.vector_test import (
        VectorTestDataset,
        VectorTestIndex,
        VectorTestItem,
        VectorTestModel,
        VectorTestSample,
        VectorTestSampleItem,
    )

    tables = [
        ModelService.__table__,
        ObjectAttributeTemplate.__table__,
        ProjectWorkJob.__table__,
        ProjectWorkItem.__table__,
        ImageSemanticJob.__table__,
        ImageSemanticItem.__table__,
        ImageSemanticVector.__table__,
        ValidationSet.__table__,
        ValidationRun.__table__,
        ValidationItem.__table__,
        GpuServer.__table__,
        TrainingRun.__table__,
        TrainingGpuHold.__table__,
        TrainingTrainer.__table__,
        TrainingAnalysis.__table__,
        ModelRepositoryItem.__table__,
        ModelRelease.__table__,
        DeploymentTarget.__table__,
        DeploymentLease.__table__,
        ModelDeployment.__table__,
        VectorTestModel.__table__,
        VectorTestIndex.__table__,
        VectorTestItem.__table__,
        VectorTestDataset.__table__,
        VectorTestSample.__table__,
        VectorTestSampleItem.__table__,
    ]
    async with test_engine.begin() as conn:
        for table in tables:
            await conn.run_sync(lambda c, t=table: t.create(c, checkfirst=True))


@pytest.mark.asyncio
async def test_script_sandbox_blocks_module_escape_and_preserves_pixel_ops():
    from services.script_sandbox import run_script, shutdown_sandbox, validate_script

    forbidden_scripts = [
        'def process(ctx):\n    return {"reason": str(Image.os.listdir("."))}\n',
        'def process(ctx):\n    return {"reason": str(Image.sys.executable)}\n',
        'def process(ctx):\n    return {"reason": str(Image.io)}\n',
        'def process(ctx):\n    img = Image.open("/etc/passwd")\n    return {"passed": True}\n',
        'def process(ctx):\n    ctx["image"].save("/tmp/out.png")\n    return {"passed": True}\n',
        'def process(ctx):\n    return {"reason": str(Image.subprocess)}\n',
        'def process(ctx):\n    return {"reason": str(Image.builtins)}\n',
    ]
    for bad in forbidden_scripts:
        with pytest.raises(ValueError):
            validate_script(bad)

    png = _make_png(80, 60)
    valid_script = '''import PIL
import PIL.Image as ImgAlias
from PIL import Image, ImageFilter, ImageOps, ImageStat
import imagehash

def process(ctx):
    canvas = Image.new("RGB", (ctx["width"], ctx["height"]), (10, 20, 30))
    blended = Image.blend(ctx["image"], canvas, 0.25)
    enhanced = ImageOps.autocontrast(blended).filter(ImageFilter.SHARPEN)
    stat = ImageStat.Stat(enhanced)
    ph = str(imagehash.phash(enhanced))
    sub = ImgAlias.new("RGB", (20, 20), (255, 0, 0))
    assert isinstance(enhanced, PIL.Image.Image)
    return {
        "passed": True,
        "image": enhanced,
        "boxes": [{"label": "car", "x": 5, "y": 5, "width": 30, "height": 20, "score": 0.88}],
        "reason": f"hash={ph[:8]},mean={int(stat.mean[0])},sub={sub.size[0]}",
    }
'''
    try:
        out = await run_script(valid_script, png, width=80, height=60)
        assert out["passed"] is True
        assert out["reason"].startswith("hash=")
        assert len(out["derived"]) == 1 and out["derived"][0]["width"] == 80
        assert out["boxes"][0]["label"] == "car"
    finally:
        await shutdown_sandbox()


@pytest.mark.asyncio
async def test_image_semantics_worker_marks_unexpected_exception_failed(
    test_engine, test_db, test_tenant, tmp_path, monkeypatch
):
    from config.settings import settings
    from models.image import LibraryImage
    from models.image_semantic import ImageSemanticItem, ImageSemanticJob
    from services import image_semantics
    from services.storage import get_storage, reset_storage

    async with test_engine.begin() as conn:
        for t in (ImageSemanticJob.__table__, ImageSemanticItem.__table__):
            await conn.run_sync(lambda c, tbl=t: tbl.create(c, checkfirst=True))

    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "STORAGE_TYPE", "local")
    reset_storage()

    raw = _make_png(32, 32)
    digest = hashlib.sha256(raw).hexdigest()
    await get_storage().save_file(BytesIO(raw), "images/sem.png")
    img = LibraryImage(
        tenant_id=test_tenant.id,
        original_name="sem.png",
        blob_path="images/sem.png",
        content_hash=digest,
        file_size=len(raw),
        width=32,
        height=32,
        format="PNG",
        source_type="upload",
    )
    test_db.add(img)
    await test_db.flush()
    job = ImageSemanticJob(
        tenant_id=test_tenant.id,
        caption_model={"id": str(uuid4()), "revision": 1, "config": {}},
        embedding_model=None,
        dimensions=2048,
        preprocessing=image_semantics.PREPROCESSING,
        prompt="",
    )
    test_db.add(job)
    await test_db.flush()
    item = ImageSemanticItem(job_id=job.id, image_id=img.id, original_name="sem.png", content_hash=digest)
    test_db.add(item)
    await test_db.commit()

    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    calls = 0

    async def flaky_validate(db, tenant_id, snapshots, lock=True):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected driver glitch")

    monkeypatch.setattr(image_semantics, "validate_snapshots", flaky_validate)
    assert await image_semantics.process_one(sessions, client=None) is True

    await test_db.refresh(item)
    assert item.status == "failed"
    assert "RuntimeError" in (item.error or "")

    # CancelledError must propagate cleanly
    item.status = "pending"
    await test_db.commit()

    async def cancel_validate(*_a, **_kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(image_semantics, "validate_snapshots", cancel_validate)
    with pytest.raises(asyncio.CancelledError):
        await image_semantics.process_one(sessions, client=None)
    reset_storage()


@pytest.mark.asyncio
async def test_vector_test_stop_unlocks_gpu_and_pull_timeout(
    client, auth_headers, test_engine, test_db, extra_tables, monkeypatch
):
    from models.training_run import TrainingRun
    from models.vector_test import VectorTestModel
    from services import gpu_reservations, vector_test

    assert vector_test.FOLLOW_DETECTION_INTERVAL == 30

    run = TrainingRun(
        tenant_id=1,
        user_id=1,
        name="wemm-merge",
        kind="model_merge",
        status="completed",
        config={"model_kind": "wemm", "output_subdir": "out"},
        source={},
        server={"id": "default", "name": "srv"},
        output_dir="/tmp/run",
        inspection={},
    )
    test_db.add(run)
    await test_db.flush()

    gpu_uuid = "GPU-11111111-2222-3333-4444-555555555555"
    m_pulling = VectorTestModel(
        tenant_id=1,
        run_id=run.id,
        name="m-pull",
        server={"id": "default", "name": "srv", "connection_key": "k1"},
        source={"server": {"id": "default", "name": "srv"}, "root": "/r", "runtime": "vllm", "artifact_path": "merged"},
        gpu=gpu_uuid,
        port=8123,
        state="pulling",
    )
    test_db.add(m_pulling)
    await test_db.commit()

    # Stop while pulling immediately clears GPU reservation
    resp = client.post(f"/api/vector-test/models/{m_pulling.id}/stop", headers=auth_headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["gpu"] is None
    assert resp.json()["state"] == "stopping"
    await gpu_reservations.assert_free(test_db, [gpu_uuid])

    # Pull timeout transitions stuck pulling model to failed and releases GPU
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(vector_test, "AsyncSessionLocal", sessions)

    m_timeout = VectorTestModel(
        tenant_id=1,
        run_id=run.id,
        name="m-timeout",
        server={"id": "default", "name": "srv", "connection_key": "k2"},
        source={
            "server": {"id": "default", "name": "srv"},
            "root": "/r",
            "runtime": "vllm",
            "artifact_path": "merged",
            "pull_deadline": int(time()) - 5,
        },
        gpu=gpu_uuid,
        port=8124,
        state="pulling",
    )
    m_pulling.state = "stopped"
    test_db.add(m_timeout)
    await test_db.commit()

    async def fake_remote(db, model, action, **_kw):
        assert not db.in_transaction() or (not db.new and not db.dirty)
        if action == "image":
            return {"ready": False}
        return {}

    monkeypatch.setattr(vector_test, "remote", fake_remote)
    assert await vector_test.operate() is True
    await test_db.refresh(m_timeout)
    assert m_timeout.state == "failed"
    assert m_timeout.gpu is None
    assert "超时" in (m_timeout.error or "")


@pytest.mark.asyncio
async def test_model_deployment_restart_recovers_transferring_and_releases_lease(
    client, auth_headers, test_engine, test_db, extra_tables, monkeypatch
):
    from models.model_deployment import DeploymentLease, ModelDeployment
    from models.training_run import TrainingRun
    from services import model_deployments

    run = TrainingRun(
        id=uuid4(),
        tenant_id=1,
        user_id=1,
        name="exp",
        kind="model_export",
        status="completed",
        config={},
        source={},
        server={},
        output_dir="/out",
        inspection={},
    )
    test_db.add(run)
    await test_db.flush()

    dep_transfer = ModelDeployment(
        id=uuid4(),
        tenant_id=1,
        user_id=1,
        run_id=run.id,
        status="transferring",
        target_key="tk-transfer",
        source={},
        target={},
        inspection={},
        logs=[],
    )
    dep_activating = ModelDeployment(
        id=uuid4(),
        tenant_id=1,
        user_id=1,
        run_id=run.id,
        status="activating",
        target_key="tk-activate",
        source={},
        target={},
        inspection={"mutation_started": True},
        logs=[],
    )
    test_db.add_all([dep_transfer, dep_activating])
    await test_db.flush()
    test_db.add_all([
        DeploymentLease(resource="lease-transfer", job_id=dep_transfer.id),
        DeploymentLease(resource="lease-activate", job_id=dep_activating.id),
    ])
    await test_db.commit()

    monkeypatch.setattr(model_deployments, "AsyncSessionLocal", async_sessionmaker(test_engine, expire_on_commit=False))
    await model_deployments.start_worker()

    await test_db.refresh(dep_transfer)
    await test_db.refresh(dep_activating)
    assert dep_transfer.status == "transfer_failed"
    assert dep_activating.status == "unknown"
    assert await test_db.get(DeploymentLease, "lease-transfer") is None
    assert await test_db.get(DeploymentLease, "lease-activate") is not None
    transfer_id = str(dep_transfer.id)
    await test_db.rollback()

    # Unlocked transfer_failed record is immediately deletable
    del_resp = client.request("DELETE", "/api/model-deployments", headers=auth_headers, json={"ids": [transfer_id]})
    assert del_resp.status_code == 200, del_resp.text


@pytest.mark.asyncio
async def test_browser_download_tickets_for_repository_and_training_artifacts(
    client, auth_headers, test_db, extra_tables, tmp_path, monkeypatch
):
    from config.settings import settings
    from models.model_repository import ModelRepositoryItem
    from models.training_run import TrainingRun
    from services import model_repository, training_runs

    monkeypatch.setattr(settings, "MODEL_ARCHIVE_PATH", str(tmp_path / "archives"))

    # 1. Model repository single & batch browser download via HttpOnly cookie without Bearer header
    payload_bytes = b"onnx-model-binary-weights"
    sha_hex = hashlib.sha256(payload_bytes).hexdigest()
    item = ModelRepositoryItem(
        id=uuid4(),
        tenant_id=1,
        user_id=1,
        name="det-v1",
        group_name="det",
        version="1.0.0",
        format="onnx",
        gpu_model="",
        tensorrt_version="",
        entry_file="model.onnx",
        file_size=len(payload_bytes),
        files=[{"path": "model.onnx", "bytes": len(payload_bytes), "sha256": sha_hex}],
        source={"kind": "upload"},
        status="ready",
    )
    test_db.add(item)
    await test_db.commit()

    files_dir = model_repository.folder(item) / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "model.onnx").write_bytes(payload_bytes)

    # Unauthenticated GET without ticket is rejected with 401
    assert client.get(f"/api/model-repository/{item.id}/browser-download").status_code == 401

    auth_single = client.post(f"/api/model-repository/{item.id}/browser-download", headers=auth_headers)
    assert auth_single.status_code == 204
    assert "repository_download" in auth_single.cookies
    client.cookies.set("repository_download", auth_single.cookies["repository_download"])
    dl_single = client.get(f"/api/model-repository/{item.id}/browser-download")
    assert dl_single.status_code == 200
    assert dl_single.content == payload_bytes

    # Batch browser download
    auth_batch = client.post(
        "/api/model-repository/download-batch/browser-download",
        headers=auth_headers,
        json={"ids": [str(item.id)]},
    )
    assert auth_batch.status_code == 204
    client.cookies.set("repository_batch_download", auth_batch.cookies["repository_batch_download"])
    dl_batch = client.get("/api/model-repository/download-batch/browser-download")
    assert dl_batch.status_code == 200
    with zipfile.ZipFile(BytesIO(dl_batch.content)) as zf:
        assert f"models/{item.id}/model.onnx" in zf.namelist()
        assert "manifest.json" in zf.namelist()

    # 2. Training artifact browser download via HttpOnly cookie
    artifact_bytes = b"checkpoint-pt-weights"
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
    run = TrainingRun(
        id=uuid4(),
        tenant_id=1,
        user_id=1,
        name="yolo-run",
        kind="yolo26",
        status="completed",
        config={"output_subdir": "exp"},
        source={},
        server={"id": "default", "name": settings.GPU_SERVER_NAME},
        output_dir="/data/runs/r1",
        inspection={},
    )
    test_db.add(run)
    await test_db.commit()

    monkeypatch.setattr(training_runs, "ROOT", tmp_path)

    async def fake_remote(db, r, action, **kw):
        assert not db.in_transaction() or (not db.new and not db.dirty)
        assert action == "artifact"
        return {
            "path": f"/data/runs/r1/exp/{kw['path']}",
            "bytes": len(artifact_bytes),
            "sha256": artifact_sha,
        }

    def fake_bridge(server, inp):
        dl = inp["download"]
        Path(dl["localPath"]).write_bytes(artifact_bytes)
        return {"success": True}

    monkeypatch.setattr(training_runs, "remote", fake_remote)
    monkeypatch.setattr(training_runs, "bridge", fake_bridge)
    monkeypatch.setattr(model_repository, "schedule_automatic", lambda *_a, **_kw: None)

    art_url = f"/api/training/runs/{run.id}/artifacts/browser-download"
    auth_art = client.post(art_url, params={"path": "weights/best.pt"}, headers=auth_headers)
    assert auth_art.status_code == 204
    client.cookies.set("training_artifact_download", auth_art.cookies["training_artifact_download"])

    # Mismatched path rejected
    assert client.get(art_url, params={"path": "weights/other.pt"}).status_code == 401
    dl_art = client.get(art_url, params={"path": "weights/best.pt"})
    assert dl_art.status_code == 200
    assert dl_art.content == artifact_bytes


@pytest.mark.asyncio
async def test_model_preview_video_caches_rendered_mp4_with_lock(
    client, auth_headers, test_db, extra_tables, monkeypatch
):
    from models.validation import ValidationItem, ValidationRun, ValidationSet
    from services import model_preview_video
    from services.storage import get_storage

    run_id = uuid4()
    source_key = f"previews/1/{run_id}/source.video"
    await get_storage().save_file(BytesIO(b"fake-source-video"), source_key)

    vset = ValidationSet(
        tenant_id=1,
        user_id=1,
        name="demo.mp4",
        labels=[],
        samples=[],
        source={"kind": "upload_preview", "video_path": source_key},
        total=1,
    )
    test_db.add(vset)
    await test_db.flush()
    vrun = ValidationRun(
        id=run_id,
        set_id=vset.id,
        tenant_id=1,
        user_id=1,
        name="demo.mp4",
        status="completed",
        model_snapshot={"preview": True, "video": {"name": "demo.mp4", "interval": 1.0, "duration": 2.0}},
        config={},
        total=1,
    )
    test_db.add(vrun)
    await test_db.flush()
    test_db.add(
        ValidationItem(
            run_id=run_id,
            asset_id=uuid4(),
            original_name="000000-0s.jpg",
            width=320,
            height=240,
            truth=[],
            predictions=[{"label": "car", "x": 10, "y": 10, "width": 50, "height": 40, "score": 0.9}],
        )
    )
    await test_db.commit()

    render_calls = 0

    def fake_render(source, items, interval, directory):
        nonlocal render_calls
        render_calls += 1
        out = Path(directory) / "result.mp4"
        out.write_bytes(b"rendered-mp4-stream")
        return out

    monkeypatch.setattr(model_preview_video, "render", fake_render)

    r1 = client.get(f"/api/model-previews/{run_id}/video", headers=auth_headers)
    r2 = client.get(f"/api/model-previews/{run_id}/video", headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.content == b"rendered-mp4-stream" and r2.content == b"rendered-mp4-stream"
    assert render_calls == 1
    cache_key = model_preview_video.cache_path(1, run_id)
    assert await get_storage().file_exists(cache_key)

    del_resp = client.delete(f"/api/model-previews/{run_id}", headers=auth_headers)
    assert del_resp.status_code == 204
    assert not await get_storage().file_exists(cache_key)


def test_auth_public_config_optional_email_rate_limit_and_user_conflict(client, auth_headers, monkeypatch):
    from api import auth_routes
    from config.settings import Settings, settings

    auth_routes._login_failures.clear()
    fresh = Settings(_env_file=None, JWT_SECRET_KEY="x" * 36)
    assert fresh.ALLOW_PUBLIC_REGISTER is False

    monkeypatch.setattr(settings, "ALLOW_PUBLIC_REGISTER", False)
    cfg = client.get("/api/auth/public-config")
    assert cfg.status_code == 200 and cfg.json() == {"allow_public_register": False}
    blocked = client.post("/api/auth/register", json={"username": "anon1", "password": "password123"})
    assert blocked.status_code == 403

    monkeypatch.setattr(settings, "ALLOW_PUBLIC_REGISTER", True)
    assert client.get("/api/auth/public-config").json() == {"allow_public_register": True}

    # Optional email (omitted & empty string) works without DB collision
    u1 = client.post("/api/auth/register", json={"username": "noemail1", "password": "password123"})
    u2 = client.post("/api/auth/register", json={"username": "noemail2", "email": "", "password": "password123"})
    assert u1.status_code == 201 and u1.json()["email"] is None
    assert u2.status_code == 201 and u2.json()["email"] is None

    # Update user to existing email returns 409 instead of 500
    conflict = client.put(
        f"/api/users/{u1.json()['id']}",
        headers=auth_headers,
        json={"email": "test@example.com"},
    )
    assert conflict.status_code == 409

    # Login sliding-window brute-force limiter
    auth_routes._login_failures.clear()
    for _ in range(auth_routes.LOGIN_MAX_FAILURES):
        res = client.post("/api/auth/login", data={"username": "testadmin", "password": "wrong-password"})
        assert res.status_code == 401
    throttled = client.post("/api/auth/login", data={"username": "testadmin", "password": "testpassword"})
    assert throttled.status_code == 429
    auth_routes._login_failures.clear()


def test_smarteye_ai_image_ssrf_and_stream_limit(monkeypatch):
    import requests
    from integrations.smarteye.api import ai_image

    for unsafe_url in [
        "http://127.0.0.1:8000/api/health",
        "http://localhost/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.115:5432/",
        "http://10.0.0.8/internal.png",
        "http://[::1]:8000/test.png",
        "file:///etc/passwd",
    ]:
        with pytest.raises(ValueError):
            ai_image._validate_public_url(unsafe_url)

    monkeypatch.setattr(
        ai_image.socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("93.184.216.34", port))],
    )

    # Redirect to loopback is blocked
    class FakeRedirectResp:
        status_code = 302
        headers = {"Location": "http://127.0.0.1:8080/private.png"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeRedirectResp())
    with pytest.raises(ValueError, match="回环|内网"):
        ai_image._fetch_external_image("https://example.com/jump.png")

    # Oversized stream is aborted immediately
    class FakeLargeStreamResp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            for _ in range(10):
                yield b"x" * chunk_size

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeLargeStreamResp())
    with pytest.raises(ValueError, match="上限"):
        ai_image._fetch_external_image("https://example.com/huge.png", max_bytes=128 * 1024)


@pytest.mark.asyncio
async def test_smarteye_scheduler_tenant_isolation(client):
    from integrations.smarteye.api import ledger_image_downloader
    from integrations.smarteye.app import app as smarteye_app
    from integrations.smarteye.hooks import install_hooks
    from integrations.smarteye.runtime import actor

    install_hooks(smarteye_app)
    flask_client = smarteye_app.test_client()

    def call_as(tenant_id: int, method: str, path: str, json_body=None):
        tok = actor.set({"tenant_id": tenant_id, "user_id": tenant_id, "role": "admin"})
        try:
            return flask_client.open(path, method=method, json=json_body)
        finally:
            actor.reset(tok)

    ledger_image_downloader._schedulers.clear()
    try:
        created = call_as(
            1,
            "POST",
            "/ledger-image/scheduler/configure",
            {"name": "租户1计划", "enabled": False, "interval_minutes": 60, "task_duration_hours": 1, "platform_ids": []},
        )
        assert created.status_code == 200 and created.get_json()["code"] == 0
        sched_id = created.get_json()["data"]["sched_id"]

        # Tenant 2 cannot see, start, stop, overwrite, or delete Tenant 1's scheduler
        status_t2 = call_as(2, "GET", "/ledger-image/scheduler/status")
        assert status_t2.status_code == 200
        assert status_t2.get_json()["data"]["schedulers"] == []

        assert call_as(2, "POST", "/ledger-image/scheduler/start", {"sched_id": sched_id}).status_code == 404
        assert call_as(2, "POST", "/ledger-image/scheduler/stop", {"sched_id": sched_id}).status_code == 404
        assert call_as(
            2,
            "POST",
            "/ledger-image/scheduler/configure",
            {"sched_id": sched_id, "name": "篡改", "interval_minutes": 60, "task_duration_hours": 1},
        ).status_code == 404
        assert call_as(2, "DELETE", f"/ledger-image/scheduler/{sched_id}").status_code == 404

        # Tenant 2 creating its own scheduler does not wipe Tenant 1's schedule
        c2 = call_as(
            2,
            "POST",
            "/ledger-image/scheduler/configure",
            {"name": "租户2计划", "enabled": False, "interval_minutes": 30, "task_duration_hours": 1, "platform_ids": []},
        )
        assert c2.status_code == 200
        status_t1 = call_as(1, "GET", "/ledger-image/scheduler/status")
        assert [s["sched_id"] for s in status_t1.get_json()["data"]["schedulers"]] == [sched_id]
    finally:
        for sid in list(ledger_image_downloader._schedulers.keys()):
            ledger_image_downloader.remove_scheduler(sid)


@pytest.mark.asyncio
async def test_workflow_dry_run_script_boxes_include_preview_overlay(
    client, auth_headers, test_db, test_project, test_asset, extra_tables
):
    from services.storage import get_storage

    png = _make_png(120, 90)
    await get_storage().save_file(BytesIO(png), test_asset.blob_path)
    test_asset.width = 120
    test_asset.height = 90
    test_asset.content_hash = hashlib.sha256(png).hexdigest()
    await test_db.commit()

    code = '''def process(ctx):
    return {
        "passed": True,
        "boxes": [{"label": "person", "x": 12, "y": 10, "width": 40, "height": 50, "score": 0.93}],
        "reason": "脚本生成检测框",
    }
'''
    resp = client.post(
        f"/api/projects/{test_project.id}/work/workflows/dry-run",
        headers=auth_headers,
        json={
            "sample_asset_ids": [str(test_asset.id)],
            "steps": [{"node_key": "s1", "title": "脚本检出", "operation": "script", "script_config": {"code": code}}],
            "edges": [],
        },
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["nodes"][0]["items"][0]
    assert item["boxes"][0]["label"] == "person"
    assert item["preview_url"].startswith("data:image/jpeg;base64,")
    assert item["width"] == 120 and item["height"] == 90
