"""Scale indexes for 100k-1M assets, annotations, events, queues, and foreign keys."""
from alembic import op
import sqlalchemy as sa

revision = '059'
down_revision = '058'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # 1. Assets composite ordering and lower(original_name) expression indexes
    op.create_index(
        'ix_assets_project_created',
        'assets',
        ['project_id', sa.text('created_at DESC'), sa.text('id DESC')],
    )
    op.create_index(
        'ix_assets_project_status_created',
        'assets',
        ['project_id', 'annotation_status', sa.text('created_at DESC'), sa.text('id DESC')],
    )
    op.create_index(
        'ix_assets_project_source_created',
        'assets',
        ['project_id', 'source_type', sa.text('created_at DESC'), sa.text('id DESC')],
    )
    op.create_index(
        'ix_assets_project_lower_name',
        'assets',
        ['project_id', sa.text('lower(original_name)')],
    )

    # 2. Missing unconditioned foreign key B-Tree indexes for fast CASCADE / SET NULL deletes
    op.create_index('ix_annotations_project_id', 'annotations', ['project_id'])
    op.create_index('ix_automation_jobs_project_id', 'automation_jobs', ['project_id'])
    op.create_index('ix_image_semantic_items_image_id', 'image_semantic_items', ['image_id'])
    op.create_index('ix_image_upload_items_image_id', 'image_upload_items', ['image_id'])
    op.create_index('ix_vector_test_items_asset_id', 'vector_test_items', ['asset_id'])
    op.create_index('ix_vector_test_samples_parent_id', 'vector_test_samples', ['parent_id'])
    op.create_index('ix_vector_test_sample_items_asset_id', 'vector_test_sample_items', ['asset_id'])

    # 3. Automation items review lookup partial index
    op.create_index(
        'ix_automation_items_ann_review',
        'automation_items',
        ['annotation_id', 'annotation_version'],
        postgresql_where=sa.text("status = 'needs_review'"),
        sqlite_where=sa.text("status = 'needs_review'"),
    )

    # 4. Annotation events project timeline index
    op.create_index(
        'ix_ann_events_project_created',
        'annotation_events',
        ['project_id', sa.text('created_at DESC'), sa.text('id DESC')],
    )

    if is_pg:
        # 5. PostgreSQL-specific expression & GIN partial indexes
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_annotations_project_review_detection "
            "ON annotations (project_id, COALESCE(review_by_kind->>'detection', review_status), created_at DESC, version DESC, id) "
            "WHERE is_latest = true"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_annotations_project_review_image "
            "ON annotations (project_id, COALESCE(review_by_kind->>'image', review_status), created_at DESC, version DESC, id) "
            "WHERE is_latest = true"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_annotations_data_gin "
            "ON annotations USING gin (annotation_data jsonb_path_ops) "
            "WHERE is_latest = true"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_ann_events_ai_pending "
            "ON annotation_events (project_id, asset_id, ((detail->>'version')::int)) "
            "WHERE action = 'ai_review' AND detail->>'decision' = 'needs_review'"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_project_work_jobs_workflow "
            "ON project_work_jobs (project_id, (options->>'workflow_id'))"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_project_work_jobs_parent "
            "ON project_work_jobs (project_id, (options->>'parent_job_id'))"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_project_work_retry_wait "
            "ON project_work_items (((result->>'retry_at')::bigint), job_id, id) "
            "WHERE status = 'retry_wait'"
        )

    # 6. Drop redundant / low-cardinality single-column indexes
    for index_name, table_name in (
        ('idx_asset_project', 'assets'),
        ('idx_annotation_asset', 'annotations'),
        ('idx_annotation_latest', 'annotations'),
        ('idx_smarteye_download_blob', 'smarteye_downloads'),
        ('idx_tenant_name', 'tenants'),
    ):
        op.execute(f'DROP INDEX IF EXISTS {index_name}')


def downgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    op.create_index('idx_tenant_name', 'tenants', ['tenant_name'])
    if sa.inspect(bind).has_table('smarteye_downloads'):
        op.create_index('idx_smarteye_download_blob', 'smarteye_downloads', ['blob_path'])
    op.create_index('idx_annotation_latest', 'annotations', ['is_latest'])
    op.create_index('idx_annotation_asset', 'annotations', ['asset_id'])
    op.create_index('idx_asset_project', 'assets', ['project_id'])

    if is_pg:
        for index_name in (
            'ix_project_work_retry_wait',
            'ix_project_work_jobs_parent',
            'ix_project_work_jobs_workflow',
            'ix_ann_events_ai_pending',
            'ix_annotations_data_gin',
            'ix_annotations_project_review_image',
            'ix_annotations_project_review_detection',
        ):
            op.execute(f'DROP INDEX IF EXISTS {index_name}')

    op.drop_index('ix_ann_events_project_created', table_name='annotation_events')
    op.drop_index('ix_automation_items_ann_review', table_name='automation_items')

    op.drop_index('ix_vector_test_sample_items_asset_id', table_name='vector_test_sample_items')
    op.drop_index('ix_vector_test_samples_parent_id', table_name='vector_test_samples')
    op.drop_index('ix_vector_test_items_asset_id', table_name='vector_test_items')
    op.drop_index('ix_image_upload_items_image_id', table_name='image_upload_items')
    op.drop_index('ix_image_semantic_items_image_id', table_name='image_semantic_items')
    op.drop_index('ix_automation_jobs_project_id', table_name='automation_jobs')
    op.drop_index('ix_annotations_project_id', table_name='annotations')

    op.drop_index('ix_assets_project_lower_name', table_name='assets')
    op.drop_index('ix_assets_project_source_created', table_name='assets')
    op.drop_index('ix_assets_project_status_created', table_name='assets')
    op.drop_index('ix_assets_project_created', table_name='assets')
