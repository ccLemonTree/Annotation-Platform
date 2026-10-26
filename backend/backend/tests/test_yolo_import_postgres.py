from alembic import command
from sqlalchemy import text,inspect
from uuid import uuid4
from tests.test_database_closeout_postgres import isolated_pg,pre_013


def test_020_preserves_upload_rows_and_defaults_to_untrusted(isolated_pg):
    engine,config=isolated_pg;pre_013(engine,config);command.upgrade(config,'019')
    with engine.connect() as db:
        before=db.scalar(text('SELECT tenant_name FROM tenants WHERE id=1'))
        columns={row['name'] for row in inspect(db).get_columns('image_uploads')}
        assert 'annotation_format' not in columns and 'trust_annotations' not in columns
    job,group=uuid4(),uuid4()
    with engine.begin() as db:
        db.execute(text("INSERT INTO users (id,tenant_id,username,email,password_hash,role,is_active) VALUES (1,1,'test','test@test.local','unused','admin',true)"))
        db.execute(text("INSERT INTO image_collections (id,tenant_id,name) VALUES (:id,1,'keep')"),dict(id=group))
        db.execute(text("INSERT INTO image_uploads (id,tenant_id,user_id,collection_id,filename,size,chunk_hashes,resume_key,min_long_edge,result) VALUES (:id,1,1,:group,'keep.zip',123,'[]','keep',0,'{}')"),dict(id=job,group=group))
    command.upgrade(config,'020')
    with engine.connect() as db:
        columns={row['name']:row for row in inspect(db).get_columns('image_uploads')}
        assert columns['annotation_format']['nullable'] and not columns['trust_annotations']['nullable']
        assert columns['trust_annotations']['default']=='false'
        assert db.scalar(text('SELECT tenant_name FROM tenants WHERE id=1'))==before
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='020'
        row=db.execute(text('SELECT filename,size,annotation_format,trust_annotations FROM image_uploads WHERE id=:id'),dict(id=job)).one()
        assert tuple(row)==('keep.zip',123,None,False)
        assert 'imported_annotation' in {row['name'] for row in inspect(db).get_columns('images')}
