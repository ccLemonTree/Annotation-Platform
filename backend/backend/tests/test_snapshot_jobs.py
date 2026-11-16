import asyncio
from io import BytesIO
from zipfile import ZipFile

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_p2_exports import populate
from tests.test_swift_export_api import swift_project


@pytest.mark.asyncio
@pytest.mark.parametrize('annotation', [False, True])
async def test_freeze_job_reports_progress_and_publishes_snapshot(
    client, auth_headers, test_project, swift_project, test_engine, monkeypatch, annotation,
):
    if annotation:
        path = f'/api/export/projects/{test_project.id}'
        body = {'dataset_format': 'infonce', 'sample_source': 'matches', 'version_label': '后台冻结'}
        preview = client.post(path + '/annotation-snapshots/preview', headers=auth_headers, json=body).json()
        body['preview_token'] = preview['preview_token']
        route = '/annotation-snapshots/jobs'
        total = 3
    else:
        from models import Project
        async with async_sessionmaker(test_engine, expire_on_commit=False)() as db:
            project = await db.get(Project, test_project.id)
            project.task_type = 'detection'
            project.label_schema = {}
            await db.commit()
        path = f'/api/export/projects/{test_project.id}'
        populate(client, auth_headers, test_project.id)
        body = {'format': 'yolo', 'labels': ['人'], 'version_label': '后台冻结'}
        route = '/snapshots/jobs'
        total = 2

    response = client.post(path + route, headers=auth_headers, json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()['id']
    assert client.post(path + route, headers=auth_headers, json=body).json()['id'] == job_id
    assert client.get(path + '/snapshot-jobs', headers=auth_headers).json()[0]['status'] == 'queued'
    assert client.get(path + '/snapshots?summary_only=true', headers=auth_headers).json() == []

    from services.storage import get_storage
    storage = get_storage()
    save = storage.save_file
    saving = asyncio.Event()
    proceed = asyncio.Event()

    async def pause_before_save(*args, **kwargs):
        saving.set()
        await proceed.wait()
        return await save(*args, **kwargs)

    monkeypatch.setattr(storage, 'save_file', pause_before_save)
    from services.snapshot_jobs import process_one
    worker = asyncio.create_task(process_one(async_sessionmaker(test_engine, expire_on_commit=False)))
    try:
        await asyncio.wait_for(saving.wait(), 5)
        progress = client.get(path + '/snapshot-jobs', headers=auth_headers).json()[0]
        assert (progress['status'], progress['packed'], progress['total']) == ('saving', total, total)
        assert client.get(path + '/snapshots?summary_only=true', headers=auth_headers).json() == []
    finally:
        proceed.set()
        await worker

    done = client.get(path + '/snapshot-jobs', headers=auth_headers).json()[0]
    assert done['status'] == 'completed' and done['id'] == job_id
    snapshots = client.get(path + '/snapshots?summary_only=true', headers=auth_headers).json()
    assert [(row['id'], row['version_label'], row['asset_count']) for row in snapshots] == [(job_id, '后台冻结', total)]
    response = client.get(f'/api/export/snapshots/{job_id}/download', headers=auth_headers)
    with ZipFile(BytesIO(response.content)) as archive:
        assert sum(name.startswith('images/') for name in archive.namelist()) == total


@pytest.mark.asyncio
async def test_freeze_job_rejects_stale_preview(client, auth_headers, swift_project, test_engine):
    project, _ = swift_project
    path = f'/api/export/projects/{project.id}'
    response = client.post(path + '/annotation-snapshots/jobs', headers=auth_headers,
        json={'dataset_format': 'infonce', 'sample_source': 'matches', 'preview_token': '0' * 64})
    assert response.status_code == 202

    from services.snapshot_jobs import process_one
    assert await process_one(async_sessionmaker(test_engine, expire_on_commit=False))
    job = client.get(path + '/snapshot-jobs', headers=auth_headers).json()[0]
    assert job['status'] == 'failed'
    assert '请重新预览' in job['error']
    assert client.get(path + '/snapshots?summary_only=true', headers=auth_headers).json() == []
