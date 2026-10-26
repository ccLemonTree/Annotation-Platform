"""SmartEye uses an isolated database and real image bytes in these tests."""
from pathlib import Path
from io import BytesIO
import pytest
from sqlalchemy import create_engine
from PIL import Image

def runtime(tmp_path, monkeypatch):
    from integrations.smarteye.runtime import Runtime, metadata
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    metadata.create_all(engine)
    from models import Base
    Base.metadata.create_all(engine,tables=[Base.metadata.tables[t] for t in ('tenants','images','image_collections','image_collection_members','image_ingest_settings')])
    instance = Runtime(engine, tmp_path / 'storage')
    return instance

def image():
    data = BytesIO()
    Image.new('RGB', (20, 15), 'red').save(data, format='PNG')
    return data.getvalue()

def test_platform_names_group_same_original_without_copy(tmp_path,monkeypatch):
    r=runtime(tmp_path,monkeypatch)
    for task,name in [('first','平台A'),('second','平台B')]:
        r.create(task,'detection',{'status':'running'},{'tenant_id':1,'user_id':1})
        r.save_image(task,r.root/'smarteye'/task/'a.png',image(),1,collection_name=name)
    from models import LibraryImage,ImageCollection,ImageCollectionMember
    from sqlalchemy import select,func
    with r.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(LibraryImage.__table__)).scalar_one()==1
        assert set(conn.execute(select(ImageCollection.__table__.c.name)).scalars())=={'平台A','平台B'}
        assert conn.execute(select(func.count()).select_from(ImageCollectionMember.__table__)).scalar_one()==2
    r.cancel('second')
    import pytest
    with pytest.raises(RuntimeError):
        r.save_image('second',r.root/'smarteye/second/late.png',image(),1,collection_name='迟到分类')

def test_task_survives_restart_and_late_cancel_does_not_win(tmp_path, monkeypatch):
    r = runtime(tmp_path, monkeypatch)
    r.create('one', 'detection', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    r.cancel('one')
    r.patch('one', {'status': 'completed', 'progress': 100}, attempt=1)
    assert r.get('one')['status'] == 'cancelled'
    assert runtime(tmp_path, monkeypatch).get('one')['status'] == 'cancelled'

def test_recover_requires_explicit_retry_and_old_attempt_cannot_write(tmp_path, monkeypatch):
    r = runtime(tmp_path, monkeypatch)
    r.create('one', 'ledger', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    r.recover()
    assert r.get('one')['status'] == 'interrupted'
    r.retry('one')
    r.patch('one', {'status': 'completed'}, attempt=1)
    assert r.get('one')['status'] == 'pending'
    assert r.get('one')['attempt'] == 2

def test_shared_files_are_hardlinks_and_cancel_rejects_late_image(tmp_path, monkeypatch):
    r = runtime(tmp_path, monkeypatch)
    r.create('one', 'detection', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    target = tmp_path / 'storage' / 'smarteye' / 'one' / 'a.png'
    key = r.save_image('one', target, image(), attempt=1)
    canonical = tmp_path / 'storage' / key
    assert canonical.samefile(target)
    from models import LibraryImage
    from sqlalchemy import select
    with r.engine.connect() as conn:
        assert conn.execute(select(LibraryImage.__table__.c.content_hash)).scalar_one() == __import__('hashlib').sha256(image()).hexdigest()
    target.unlink()
    assert canonical.read_bytes() == image()
    r.cancel('one')
    with pytest.raises(RuntimeError):
        r.save_image('one', target, image(), attempt=1)

def test_invalid_image_cannot_enter_storage(tmp_path, monkeypatch):
    r = runtime(tmp_path, monkeypatch)
    r.create('one', 'tool', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    with pytest.raises(Exception):
        r.save_image('one', tmp_path / 'storage' / 'smarteye' / 'bad.jpg', b'not image', attempt=1)
    assert not list((tmp_path / 'storage').rglob('*.jpg'))

def test_replacing_alias_preserves_old_original_and_updates_reference(tmp_path, monkeypatch):
    from integrations.smarteye.runtime import files
    from sqlalchemy import select
    r = runtime(tmp_path, monkeypatch)
    r.create('one', 'ledger', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    target = tmp_path / 'storage/smarteye/one/a.png'
    old_key = r.save_image('one', target, image(), attempt=1)
    blue = BytesIO(); Image.new('RGB', (20, 15), 'blue').save(blue, format='PNG')
    new_key = r.save_image('one', target, blue.getvalue(), attempt=1)
    assert (r.root / old_key).read_bytes() == image()
    assert target.samefile(r.root / new_key)
    with r.engine.connect() as conn:
        assert conn.execute(select(files.c.blob_path)).scalar_one() == new_key

def test_crop_temporary_original_is_not_associated(tmp_path, monkeypatch):
    r = runtime(tmp_path, monkeypatch)
    r.create('crop', 'ledger', {'status': 'running'},
             {'tenant_id': 1, 'user_id': 1, 'project_id': 'test-project'})
    associations = []
    monkeypatch.setattr(r, '_associate', lambda *args: associations.append(args))
    r.save_image('crop', r.root / 'smarteye/crop/original.png', image(), 1, associate=False)
    assert not associations
    r.save_image('crop', r.root / 'smarteye/crop/crop.png', image(), 1)
    assert len(associations) == 1

def test_reloaded_tasks_support_membership_for_progress(tmp_path, monkeypatch):
    from integrations.smarteye import runtime as module
    r = runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(module, '_runtime', r)
    r.create('tool', 'tool', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    assert 'tool' in module.DurableTasks('tool')
    assert 'tool' not in module.DurableTasks('ledger')

def test_resolution_skips_final_image_but_preserves_crop_input(tmp_path,monkeypatch):
    r=runtime(tmp_path,monkeypatch)
    r.create('filtered','ledger',{'status':'running'},{'tenant_id':1,'user_id':1,'min_long_edge':720})
    target=r.root/'smarteye/filtered/final.png'
    assert r.save_image('filtered',target,image(),1) is None
    assert not target.exists() and not list((r.root/'images').rglob('*.png'))
    assert r.get('filtered')['filtered_count']==1
    assert r.save_image('filtered',r.root/'smarteye/filtered/temp.png',image(),1,associate=False)
    r.cancel('filtered')
    with pytest.raises(RuntimeError):
        r.save_image('filtered',target,image(),1)
    assert r.get('filtered')['filtered_count']==1

def test_smarteye_queues_scene_without_new_original_copy(tmp_path,monkeypatch):
    from models import ImageIngestSettings,LibraryImage
    from schemas.image_ingest import ImageIngestConfig
    from sqlalchemy import insert,select
    r=runtime(tmp_path,monkeypatch)
    with r.engine.begin() as conn:
        conn.execute(insert(ImageIngestSettings.__table__).values(tenant_id=1,revision=1,
            config=ImageIngestConfig(classification_enabled=True).model_dump()))
    r.create('scene','detection',{'status':'running'},{'tenant_id':1,'user_id':1})
    key=r.save_image('scene',r.root/'smarteye/scene/image.png',image(),1)
    with r.engine.connect() as conn:
        assert conn.execute(select(LibraryImage.__table__.c.scene_status)).scalar_one()=='pending'
    assert (r.root/key).samefile(r.root/'smarteye/scene/image.png')

@pytest.mark.parametrize('size',[(720,30),(30,720),(720,720)])
def test_smarteye_accepts_long_edge_equal_to_threshold(tmp_path,monkeypatch,size):
    r=runtime(tmp_path,monkeypatch)
    r.create('boundary','detection',{'status':'running'},{'tenant_id':1,'user_id':1,'min_long_edge':720})
    data=BytesIO();Image.new('RGB',size,'blue').save(data,format='PNG')
    target=r.root/'smarteye/boundary/image.png'
    key=r.save_image('boundary',target,data.getvalue(),1)
    assert key and target.samefile(r.root/key)
    assert r.get('boundary').get('filtered_count',0)==0

def test_smarteye_resolves_collection_rules_and_freezes_task_policy(tmp_path,monkeypatch):
    from models import ImageCollection,LibraryImage,ImageCollectionMember
    from schemas.image_ingest import ImageIngestConfig
    from sqlalchemy import insert,select,update,func
    import uuid
    r=runtime(tmp_path,monkeypatch);a,b=uuid.uuid4(),uuid.uuid4()
    with r.engine.begin() as conn:
        for id,name,minimum in ((a,'platform-a',20),(b,'platform-b',30)):
            conn.execute(insert(ImageCollection.__table__).values(id=id,tenant_id=1,name=name,
                ingest_config=ImageIngestConfig(min_long_edge=minimum).model_dump(),ingest_revision=1))
    r.create('multi','detection',{'status':'running'},{'tenant_id':1,'user_id':1})
    key=r.save_image('multi',r.root/'smarteye/multi/a.png',image(),1,collection_name='platform-a')
    assert key
    assert r.save_image('multi',r.root/'smarteye/multi/b.png',image(),1,collection_name='platform-b') is None
    with r.engine.begin() as conn:
        conn.execute(update(ImageCollection.__table__).where(ImageCollection.id==a).values(ingest_config=ImageIngestConfig(min_long_edge=100).model_dump(),ingest_revision=2))
    assert r.save_image('multi',r.root/'smarteye/multi/again.png',image(),1,collection_name='platform-a')==key
    r.create('target','detection',{'status':'running'},{'tenant_id':1,'user_id':1,'collection_id':str(b)})
    assert r.save_image('target',r.root/'smarteye/target/a.png',image(),1,collection_name='platform-a') is None
    with r.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(LibraryImage.__table__)).scalar_one()==1
        assert conn.execute(select(func.count()).select_from(ImageCollectionMember.__table__)).scalar_one()==1
