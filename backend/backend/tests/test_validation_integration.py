"""Validation schema and shared-original retention; isolated databases only."""
from pathlib import Path
import importlib.util
import pytest
from sqlalchemy import inspect, text, UniqueConstraint
from tests.test_database_closeout_postgres import isolated_pg, pre_013
from tests.test_storage_cleanup import cleanup_client, original


def test_validation_routes_are_registered_under_one_api_prefix():
    from main import app
    paths = app.openapi()['paths']
    assert '/api/validation/sets' in paths
    assert '/api/validation/runs/{run_id}/items/{item_id}/image' in paths
    assert not any('/api/api/validation' in path for path in paths)


def test_validation_migration_is_additive_and_chained():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/016_model_validation.py'
    assert path.exists(), '缺少验证集增量迁移'
    spec = importlib.util.spec_from_file_location('validation_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == '016' and module.down_revision == '015'
    assert set(module.TABLES) == {'validation_sets', 'validation_runs', 'validation_items'}


def test_validation_samples_participate_in_storage_references():
    from services.storage_cleanup import _reference_queries
    names = {name for name, _ in _reference_queries(False, validation_exists=True)}
    assert 'validation_set' in names


def test_016_schema_matches_models_and_preserves_existing_rows(isolated_pg):
    from alembic import command
    from models import Base
    engine, config = isolated_pg
    pre_013(engine, config)
    command.upgrade(config, '015')
    with engine.connect() as connection:
        names_before = inspect(connection).get_table_names()
        before = {name: connection.execute(text('SELECT count(*) FROM "' + name + '"')).scalar_one()
                  for name in names_before if name != 'alembic_version'}
    command.upgrade(config, '016')
    with engine.connect() as connection:
        inspector = inspect(connection)
        for name in ('validation_sets', 'validation_runs', 'validation_items'):
            expected = Base.metadata.tables[name]
            columns = inspector.get_columns(name)
            assert {column['name'] for column in columns} == set(expected.c.keys())
            for column in columns:
                assert column['nullable'] == expected.c[column['name']].nullable
                assert column['type'].compile(dialect=connection.dialect) == expected.c[column['name']].type.compile(dialect=connection.dialect)
            assert {index['name'] for index in inspector.get_indexes(name) if not index.get('duplicates_constraint')} == {index.name for index in expected.indexes}
            assert {item['name'] for item in inspector.get_unique_constraints(name)} == {constraint.name for constraint in expected.constraints if isinstance(constraint, UniqueConstraint)}
            assert len(inspector.get_foreign_keys(name)) == len(expected.foreign_key_constraints)
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '016'
        for name, count in before.items():
            assert connection.scalar(text('SELECT count(*) FROM "' + name + '"')) == count
    with pytest.raises(RuntimeError, match='禁止|refus'):
        command.downgrade(config, '015')


def test_016_refuses_existing_table_without_partial_upgrade(isolated_pg):
    from alembic import command
    engine, config = isolated_pg
    pre_013(engine, config)
    command.upgrade(config, '015')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE validation_sets (marker TEXT)'))
        connection.execute(text("INSERT INTO validation_sets VALUES ('preserve')"))
    with pytest.raises(RuntimeError, match='已有|覆盖|refus'):
        command.upgrade(config, '016')
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT marker FROM validation_sets')) == 'preserve'
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '015'
        assert not set(('validation_runs', 'validation_items')) & set(inspect(connection).get_table_names())


@pytest.mark.asyncio
async def test_validation_snapshot_protects_unlinked_original(cleanup_client, auth_headers, test_db, test_project):
    from models.validation import ValidationSet
    connection = await test_db.connection()
    await connection.run_sync(lambda sync: ValidationSet.__table__.create(sync, checkfirst=True))
    key = original(cleanup_client[1])
    test_db.add(ValidationSet(tenant_id=1, project_id=test_project.id, user_id=1, name='pinned truth',
        labels=['person'], total=1, source={}, samples=[{'blob_path': key, 'content_hash': 'a' * 64}]))
    await test_db.commit()
    response = cleanup_client[0].get('/api/storage-maintenance/preview', headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()['candidate_count'] == 0
    assert response.json()['protected_counts']['validation_set'] == 1
